# Course Overview

Embedded Linux System Internals is a comprehensive 5-day hands-on program (35 hours) for embedded engineers who want to build, debug, and optimize Linux-based systems with confidence — covering the full flow from bootloader to drivers. HRDC Registration No: 10001688034.

Held at Timmins Training Center, Penang on 3–7 August 2026. Uses BeagleBone Black as the target hardware platform.

A complimentary pre-session is included for registered participants covering Linux command-line refresh and C/C++ embedded concepts preparation.

Who should attend:
- Embedded software engineers
- Firmware engineers
- Linux system developers
- Device driver developers
- Engineers working with embedded Linux platforms

Prerequisites:
- Embedded programming in C or C++
- Basic Linux knowledge

The trainer has 18+ years of hands-on embedded Linux experience, having trained teams at Qualcomm, Motorola, Robert Bosch, Philips, LG Soft, John Deere, Capgemini, Intel, and Sierra Wireless.

Course structure (5 days, 10 sessions):

Day 1 — Boot & Build:
- Session 1: Booting Initialization — embedded system components, BeagleBone Black boot stages, boot with default images; hands-on: set up and boot the board
- Session 2: Buildroot — Linux package configuration, Buildroot overview and configuration; hands-on: build rootfs/kernel/u-boot with Buildroot, add SSH support, configure auto-start on boot

Day 2 — Bootloader & Kernel:
- Session 3: Bootloaders — bootloader design, first-stage bootloader, U-Boot, adding custom U-Boot commands; hands-on: add a custom command in U-Boot
- Session 4: Linux Kernel — kernel tasks, boot parameters, configuring and building the kernel; hands-on: build statically, create ramdisk, boot custom kernel

Day 3 — Drivers:
- Session 5: Character Driver — major/minor numbers, registering drivers, udev, dynamic device file creation, IOCTL; hands-on: write character driver, exchange data with user space, use IOCTL
- Session 6: Devices & Drivers in Linux — device model, Linux driver model, platform devices & drivers, Device Tree Blob (DTB); hands-on: write platform driver, add DTB entry, test via application

Day 4 — Synchronization & Interrupts:
- Session 7: Synchronization Mechanisms — atomic operations, mutex, semaphore, spinlocks; hands-on: driver demos for each mechanism
- Session 8: Interrupt Management — IRQ numbers, interrupt registration, SoftIRQ, tasklets, work queues; hands-on: GPIO interrupt driver, bottom half implementation

Day 5 — DMA & Debugging:
- Session 9: Linux DMA Engine — DMA controllers, transfer types, DMA Engine APIs; hands-on: enhance SPI driver with DMA
- Session 10: Linux Kernel Debugging — dynamic debugging, DebugFS, KGDB, oops analysis, Kprobes/Kretprobes, Ftrace; hands-on: DebugFS interface, Kprobes, KGDB source-level debugging

Key topics:
- Embedded Linux boot sequence: ROM → bootloader → kernel → drivers
- U-Boot configuration, custom commands, board recovery
- Buildroot for generating rootfs, kernel, and bootloader images
- Linux kernel configuration and custom build for target hardware
- Character drivers and platform drivers with Device Tree integration
- Synchronization: mutex, semaphore, spinlocks for concurrent driver code
- Interrupt handling: top-half and bottom-half (tasklets, work queues)
- Linux DMA Engine APIs for high-speed data transfers
- Kernel debugging: DebugFS, KGDB, Ftrace, Kprobes, oops analysis

Tools used: Linux, U-Boot, Buildroot, BeagleBone Black, Device Tree, GCC, GDB, DebugFS, C/C++.

Practical outcomes:
- Explain the full embedded Linux boot process
- Build and boot custom Linux images using Buildroot
- Develop and test character and platform drivers
- Use Device Tree Blob for hardware-driver integration
- Apply synchronization mechanisms in driver code
- Implement interrupt handling with deferred work
- Debug kernel-level issues using professional Linux tools

Investment: RM 7,800 per participant. Group discount available for minimum 3 pax. 100% HRDC claimable.
