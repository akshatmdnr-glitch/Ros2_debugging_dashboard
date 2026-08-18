"""Runtime telemetry (Phase 3).

The graph (Phase 1) says what exists; attribution (Phase 2) says what belongs
to what; telemetry says what is actually happening.

Like attribution.py, this module has NO ROS/rclpy imports. It is pure data and
decision logic. The ROS-facing collector provides subscriptions and streams TF
transforms; this layer decides what to watch, records the numbers, and leaves
all judgment ("is this abnormal?") to a later diagnostic phase.

Deliberate design decisions (see Phase 3 notes):
  * We watch a SELECTED set of topics (attributed + explicit allowlist), never
    everything. Reasons: CPU, bandwidth, large/high-frequency payloads, and QoS
    compatibility are the monitor's own footprint on the system it observes.
  * Monitoring subscriptions use BEST_EFFORT/VOLATILE (maximally compatible),
    decided by the collector; here we only record what was chosen and why.
  * Payloads are discarded; we count messages and track time. No content.
  * Process state (/proc) is OUT-OF-BAND: DDS knows nothing about OS resources.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ros2_debugger.attribution import SOURCE_MIXED, PENDING
from ros2_debugger.model import TopicInfo

# Topics that are infrastructure of the graph itself, not application data.
INFRASTRUCTURE_TOPICS = frozenset(
    {"/rosout", "/tf", "/tf_static", "/parameter_events", "/clock"}
)

# Action interfaces create an internal topic set under the action name.
ACTION_TOPIC_MARKER = "/_action/"


def _is_action_topic(name: str) -> bool:
    return ACTION_TOPIC_MARKER in name


@dataclass(frozen=True)
class TelemetryConfig:
    """Which systems/topics/processes the debugger monitors.

    A `processes` entry may be a plain command-line pattern string, or a dict
    that additionally declares an optional owner so Phase 5 can attribute a
    resource diagnostic to a robot:
        processes:
          - pattern: "lib/ros2_debugger/debugger"
          - pattern: "nav"
            system: warehouse
            robot: robot2
    Ownership is deployment data (like attribution); an absent owner leaves the
    resource diagnostic entity-less.
    """

    monitor_systems: Optional[Tuple[str, ...]] = None  # None = all attributed
    monitor_topics: Tuple[str, ...] = ()
    processes: Tuple[str, ...] = ()
    process_owners: Dict[str, Tuple[Optional[str], Optional[str]]] = field(
        default_factory=dict
    )

    @classmethod
    def from_dict(cls, data: dict) -> "TelemetryConfig":
        data = data or {}
        systems = data.get("monitor_systems")
        patterns: List[str] = []
        owners: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        for entry in data.get("processes") or ():
            if isinstance(entry, dict):
                pattern = entry["pattern"]
                patterns.append(pattern)
                owners[pattern] = (entry.get("system"), entry.get("robot"))
            else:
                patterns.append(str(entry))
        return cls(
            monitor_systems=tuple(systems) if systems else None,
            monitor_topics=tuple(data.get("monitor_topics") or ()),
            processes=tuple(patterns),
            process_owners=owners,
        )


@dataclass
class TopicStats:
    topic: str
    type: Optional[str] = None
    monitored: bool = False
    receiving: bool = False  # has our subscription ever delivered a message
    message_count: int = 0
    rate_hz: float = 0.0
    last_message_time: Optional[float] = None  # monotonic
    idle_seconds: Optional[float] = None  # now - last_message_time at last sample
    publisher_reliability: str = ""
    publisher_durability: str = ""
    monitored_cycles: int = 0  # sample cycles since this topic was subscribed
    reason: str = ""  # why not monitored, or current status note


@dataclass
class ProcessStats:
    pattern: str
    pids: List[int] = field(default_factory=list)
    alive: bool = False
    cpu_percent: float = 0.0  # aggregate across matching pids (can exceed 100)
    rss_mb: float = 0.0  # max VmRSS across matching pids
    sampled_at: float = 0.0


@dataclass
class FrameStats:
    frame_id: str
    count: int = 0
    last_stamp_sec: float = 0.0  # ROS stamp of latest transform
    last_seen: float = 0.0  # monotonic time of last receipt


class TfStats:
    """Per-frame transform freshness AND the parent/child frame tree.

    Fed by the collector's /tf and /tf_static streams. Each transform is a
    (parent, child) pair; we keep per-frame stats for both sides and the
    child -> parent relationship so the dashboard can draw the TF tree.
    """

    def __init__(self) -> None:
        self._frames: Dict[str, FrameStats] = {}
        self._parents: Dict[str, str] = {}  # child -> parent
        self._edge_seen: Dict[Tuple[str, str], float] = {}

    def record(self, parent: str, child: str, stamp_sec: float, now: float) -> None:
        for frame_id in (parent, child):
            if not frame_id:
                continue
            frame = self._frames.setdefault(frame_id, FrameStats(frame_id=frame_id))
            frame.count += 1
            frame.last_stamp_sec = max(frame.last_stamp_sec, stamp_sec)
            frame.last_seen = now
        if parent and child:
            self._parents[child] = parent
            self._edge_seen[(parent, child)] = now

    @property
    def frames(self) -> List[FrameStats]:
        return sorted(self._frames.values(), key=lambda f: f.frame_id)

    @property
    def edges(self) -> List[Tuple[str, str]]:
        """(parent, child) transform pairs observed on the wire, ordered for a
        stable tree layout (parent first, then child)."""
        pairs = [(parent, child) for child, parent in self._parents.items()]
        return sorted(pairs, key=lambda kv: (kv[0], kv[1]))

    @property
    def total_transforms(self) -> int:
        return sum(f.count for f in self._frames.values())


class TopicMonitor:
    """Decides what to monitor and keeps per-topic runtime statistics.

    reconcile() is called once per graph refresh. It diffs the desired topic
    set against current subscriptions via a ROS-facing collector, updates
    stats, and recomputes rates on the sampling cadence.
    """

    def __init__(self, config: TelemetryConfig) -> None:
        self.config = config
        self._stats: Dict[str, TopicStats] = {}
        self._count_at_sample: Dict[str, int] = {}
        self._last_sample: Optional[float] = None

    def stats(self) -> List[TopicStats]:
        return sorted(self._stats.values(), key=lambda s: s.topic)

    def reconcile(
        self, graph, system_model, collector, now: float
    ) -> None:
        if os.environ.get("DEBUG_TELEMETRY"):
            for t in graph.topics:
                eps = [e.node.fully_qualified_name for e in t.publishers + t.subscribers]
                print(
                    f"[telem-debug] {t.name} eps={eps} "
                    f"attr={system_model.attribute_topic(t)}",
                    flush=True,
                )
        desired: Dict[str, TopicInfo] = {}
        for topic in graph.topics:
            if topic.name in INFRASTRUCTURE_TOPICS or _is_action_topic(topic.name):
                self._note(topic, "infrastructure")
                continue
            attr = system_model.attribute_topic(topic)
            if attr is PENDING:
                # Endpoint node info not yet resolved (rmw race). Try the
                # low-confidence topic-name convention; if that also fails,
                # keep retrying next cycle -- never permanently discard.
                attr = system_model.attributor.attribute_topic_name(topic.name)
                if attr.is_unclassified:
                    self._note(topic, "waiting for publisher node info")
                    continue
            elif attr.is_unclassified:
                self._note(topic, "unattributed")
                continue
            elif attr.source == SOURCE_MIXED:
                self._note(topic, "shared across owners")
                continue
            if self.config.monitor_systems and attr.system not in self.config.monitor_systems:
                self._note(topic, f"system '{attr.system}' not in monitor scope")
                continue
            desired[topic.name] = topic

        for name in self.config.monitor_topics:
            if name not in desired:
                topic = graph.get_topic(name)
                if topic is not None:
                    desired[name] = topic
                else:
                    self._note_name(name, "explicitly requested but not on the graph")

        # Subscribe to anything newly desired; refresh observed QoS.
        for name, topic in desired.items():
            stat = self._stats.setdefault(
                name, TopicStats(topic=name, type=topic.primary_type)
            )
            if topic.publishers:
                p = topic.publishers[0]
                stat.publisher_reliability = p.reliability
                stat.publisher_durability = p.durability
            if not stat.monitored:
                subscribed = collector.ensure_topic_subscription(
                    name, topic.primary_type or "", self._counter_for(name)
                )
                stat.monitored = subscribed
                stat.reason = "subscribed" if subscribed else "cannot subscribe (unknown type)"

        # Drop subscriptions no longer desired.
        for name in list(self._stats):
            stat = self._stats[name]
            if stat.monitored and name not in desired:
                collector.drop_topic_subscription(name)
                stat.monitored = False
                stat.reason = "no longer present/attributed"

        self.sample(now)

    def sample(self, now: float) -> None:
        """Recompute rates/idle over the interval since the last sample.

        Rates are only recomputed over a meaningful window (>= 0.25 s). The
        reconcile right after spinning stops sees a degenerate sub-window with
        no message processing; reporting a rate there would falsely show a
        drop. Idle time is always updated.
        """
        if self._last_sample is not None and now > self._last_sample:
            dt = now - self._last_sample
            if dt >= 0.25:
                for name, stat in self._stats.items():
                    if stat.monitored:
                        previous = self._count_at_sample.get(name, 0)
                        stat.rate_hz = max(0.0, (stat.message_count - previous) / dt)
            for name, stat in self._stats.items():
                if stat.last_message_time is not None:
                    stat.idle_seconds = now - stat.last_message_time
        for name, stat in self._stats.items():
            if stat.monitored:
                stat.monitored_cycles += 1
        self._count_at_sample = {
            name: s.message_count for name, s in self._stats.items() if s.monitored
        }
        self._last_sample = now

    def _note(self, topic: TopicInfo, reason: str) -> None:
        stat = self._stats.setdefault(
            topic.name, TopicStats(topic=topic.name, type=topic.primary_type)
        )
        if not stat.monitored:
            stat.reason = reason

    def _note_name(self, name: str, reason: str) -> None:
        stat = self._stats.setdefault(name, TopicStats(topic=name))
        if not stat.monitored:
            stat.reason = reason

    def _counter_for(self, topic: str) -> Callable:
        def on_message(_msg) -> None:
            stat = self._stats.get(topic)
            if stat is None or not stat.monitored:
                return
            stat.message_count += 1
            stat.receiving = True
            stat.last_message_time = time.monotonic()
        return on_message


class ProcessMonitor:
    """Out-of-band OS process sampling via /proc (Decision D2).

    Matches processes by command-line substring. CPU% = delta of utime+stime
    (in clock ticks) over delta of wall time. PID reuse is guarded with the
    process starttime from /proc/<pid>/stat.
    """

    def __init__(self, patterns: Tuple[str, ...]) -> None:
        self._stats = {p: ProcessStats(pattern=p) for p in patterns}
        self._prev: Dict[int, Tuple[float, float, int]] = {}  # pid -> (ticks, wall, starttime)
        self._clk_tck = os.sysconf(os.sysconf_names["SC_CLK_TCK"])  # type: ignore[arg-type]

    def stats(self) -> List[ProcessStats]:
        return sorted(self._stats.values(), key=lambda s: s.pattern)

    def sample(self, now: float) -> None:
        matches = self._find_matching_pids()
        for pattern, stat in self._stats.items():
            pids = matches.get(pattern, [])
            stat.pids = pids
            stat.alive = bool(pids)
            cpu = 0.0
            rss = 0.0
            for pid in pids:
                read = self._read_proc(pid)
                if read is None:
                    continue
                ticks, rss_kb, starttime = read
                rss = max(rss, rss_kb / 1024.0)
                prev = self._prev.get(pid)
                if prev is not None and prev[2] == starttime:
                    d_ticks = ticks - prev[0]
                    d_wall = now - prev[1]
                    if d_wall > 0:
                        cpu += max(0.0, d_ticks / self._clk_tck / d_wall * 100.0)
                self._prev[pid] = (ticks, now, starttime)
            stat.cpu_percent = cpu
            stat.rss_mb = rss
            stat.sampled_at = now

    def _find_matching_pids(self) -> Dict[str, List[int]]:
        out: Dict[str, List[int]] = {p: [] for p in self._stats}
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            cmdline = _read_cmdline(pid_dir)
            if not cmdline:
                continue
            for pattern in self._stats:
                if pattern.encode() in cmdline:
                    out[pattern].append(int(pid_dir))
        return out

    def _read_proc(self, pid: int):
        try:
            with open(f"/proc/{pid}/stat", "r") as f:
                data = f.read()
            close = data.rfind(")")
            fields = data[close + 2:].split()
            utime = int(fields[11])  # field 14 overall
            stime = int(fields[12])  # field 15 overall
            starttime = int(fields[19])  # field 22 overall
            rss_kb = 0
            with open(f"/proc/{pid}/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss_kb = int(line.split()[1])
                        break
            return utime + stime, rss_kb, starttime
        except (OSError, ValueError, IndexError):
            return None


def _read_cmdline(pid_dir: str) -> bytes:
    try:
        with open(f"/proc/{pid_dir}/cmdline", "rb") as f:
            return f.read().replace(b"\x00", b" ")
    except OSError:
        return b""


class TelemetryModel:
    """Aggregates the runtime telemetry the debugger observes."""

    def __init__(self, config: TelemetryConfig) -> None:
        self.config = config
        self.topics = TopicMonitor(config)
        self.processes = ProcessMonitor(config.processes)
        self.tf = TfStats()
        self.sampled_at = 0.0

    def reconcile(
        self, collector, system_model, graph, now: float
    ) -> None:
        self.topics.reconcile(graph, system_model, collector, now)
        self.processes.sample(now)
        self.sampled_at = now
