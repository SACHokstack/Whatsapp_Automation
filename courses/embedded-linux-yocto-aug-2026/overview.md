# Course Overview

Embedded Linux with Yocto: Deep Foundation & Customization is a 5-day hands-on course (35 hours) for engineers who want to understand the full Embedded Linux boot flow, build Yocto-based systems, customize layers, and configure bootloader, kernel, and root filesystem components for target hardware. HRDC Registration No: 10001702890.

Held at Timmins Training Center, Penang on 10–14 August 2026. Uses BeagleBone Black as the target hardware.

Who should attend:
- Embedded software engineers
- Firmware engineers
- Linux system developers
- BSP/platform engineers
- Engineers working with embedded Linux platforms
- Engineers who need Yocto-based build and customization skills

Prerequisites:
- Comfortable with Linux environment and basic commands
- Basic Embedded Linux knowledge is helpful
- Familiarity with bootloader, kernel, or RootFS concepts is useful
- Ubuntu 22.04+ build environment is recommended (PC or VM with 150 GB disk, 8 GB RAM)

Course structure (5 days):

Days 1–2 — Embedded Linux Concepts:
- Booting Initialization: boot flow overview (ROM → Bootloader → Kernel), BeagleBone Black boot up; hands-on: boot the board with default images
- Bootloaders: U-Boot overview, bootloader designs, custom commands, U-Boot environment, board recovery; hands-on: add custom U-Boot command, recover bricked board
- Linux Kernel: kernel architecture, kernel space vs user space, kernel configuration and build; hands-on: configure and build the kernel

Days 3–4 — Yocto Fundamentals:
- Yocto Build System: Yocto Project overview, architecture, OpenEmbedded vs Poky vs Yocto, BitBake and configuration files; hands-on: set up bare BitBake, build a recipe
- BitBake Metadata: recipes, bbclass, layers, BitBake functions & tasks, variables, bbappend, dependency graphs, debug statements; hands-on: create a Yocto layer, write HelloWorld recipe, extend recipes, troubleshoot build failures
- Building for Target Hardware: host environment setup, Yocto image and BSP layer intro, download and configure Yocto sources, build console image, cross-compilation toolchains; hands-on: build and test image on BeagleBone Black
- Yocto Meta Layers: types of layers, BSP layer introduction, BSP source code walkthrough; hands-on: create meta layer, create custom BSP layer, add new machine

Day 5 — Customizing Kernel, Bootloader & RootFS:
- Linux Kernel & Bootloader with Yocto: U-Boot configuration and building via Yocto, kernel configuration/build/update, recipes-kernel in BSP layer, appending/extending kernel recipes, applying patches; hands-on: build U-Boot, apply custom bootloader patches, apply kernel configuration fragments, create and apply kernel patches
- Linux Root Filesystem: rootfs concepts, adding/removing packages, custom applications, init managers (SysV init / Systemd); hands-on: create custom rootfs image, add/remove packages, include custom applications

Key topics:
- Complete embedded Linux boot flow: ROM → U-Boot → kernel → rootfs
- U-Boot: bootloader design, custom commands, environment variables, board recovery
- Yocto Project architecture: Poky, OpenEmbedded, BitBake
- BitBake recipes, bbclass, variables, layers, bbappend, task execution
- Meta layer and BSP layer creation and customization
- Building and testing embedded Linux images on BeagleBone Black
- Kernel configuration, patching, and building with Yocto
- Root filesystem package management and custom application inclusion
- Cross-compilation toolchains

Tools used: Yocto, BitBake, Poky, OpenEmbedded, U-Boot, Linux Kernel, RootFS, BSP Layers, Recipes, BeagleBone Black.

Practical outcomes:
- Build and test embedded Linux systems using Yocto
- Create Yocto meta layers, BSP customizations, and machine configurations
- Configure, patch, and update U-Boot and Linux kernel components via Yocto
- Add/remove packages and include custom applications in root filesystem

Investment: RM 7,500 per participant. Group discount available for minimum 3 pax. 100% HRDC claimable.
