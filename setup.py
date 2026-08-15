from setuptools import find_packages, setup

package_name = "ros2_debugger"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools", "fastapi", "uvicorn"],
    include_package_data=True,
    package_data={"ros2_debugger": ["config/*.yaml"]},
    zip_safe=True,
    maintainer="akshat",
    maintainer_email="akshat@example.com",
    description="ROS 2 debugging and observability platform - Phase 1 collector",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "debugger = ros2_debugger.debugger:main",
            "debugger-api = ros2_debugger.api:main",
        ],
    },
)
