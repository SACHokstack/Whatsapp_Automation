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

SECTION X – EMBEDDED LINUX WITH YOCTO: DEEP FOUNDATION & CUSTOMIZATION KNOWLEDGE BASE
Q1. What is this course about?
Answer:
Embedded Linux with Yocto: Deep Foundation & Customization is a 5-day hands-on training that teaches engineers how to build, customize, and maintain Embedded Linux systems using the Yocto Project.
Participants learn the complete Embedded Linux build workflow—from bootloader and kernel to root filesystem—while mastering Yocto architecture, BitBake, recipes, layers, BSP customization, and system integration using the BeagleBone Black platform.
Q2. Who should attend?
Answer:
This course is suitable for:
Embedded Software Engineers
Firmware Engineers
Linux System Developers
BSP / Platform Engineers
Device Driver Developers
Embedded Linux Engineers
Engineers responsible for Linux platform customization
Q3. Is this suitable for beginners?
Answer:
The course is designed for engineers who are already comfortable with Linux commands and have basic Embedded Linux knowledge. Previous Yocto experience is not required.
Q4. What are the prerequisites?
Answer:
Participants should have:
Basic Linux command-line knowledge
Comfortable working in a Linux environment
Basic Embedded Linux knowledge
Familiarity with bootloader, Linux kernel, or RootFS concepts is helpful
For lab work, Ubuntu 22.04 or later is recommended.
Q5. What hardware platform is used?
Answer:
The hands-on laboratories use the BeagleBone Black embedded development board as the target platform for building and testing Embedded Linux systems.
Q6. What is Yocto?
Answer:
The Yocto Project is an open-source build system that allows developers to create customized Embedded Linux distributions for specific hardware platforms.
Instead of installing packages manually, Yocto builds the entire operating system—including the bootloader, kernel, libraries, applications, and root filesystem—from source.
Q7. What is BitBake?
Answer:
BitBake is the build engine used by the Yocto Project.
It executes recipes, manages dependencies, processes build tasks, and generates complete Linux images for embedded systems.
Participants learn BitBake fundamentals, configuration files, variables, tasks, classes, and recipe extensions.
Q8. Will Yocto fundamentals be covered?
Answer:
Yes.
Participants learn:
Yocto architecture
OpenEmbedded
Poky
BitBake
Configuration files
Build workflow
Yocto project structure
Q9. Will recipes and layers be covered?
Answer:
Yes.
The course covers:
Recipes
bbclass
Layers
BSP Layers
Meta Layers
Variables
Tasks
Recipe dependencies
bbappend extensions
Participants also create their own Yocto layer and recipes.
Q10. Will BSP customization be covered?
Answer:
Yes.
Participants learn how to:
Create BSP layers
Customize board support packages
Add new machines
Configure platform-specific settings
Modify machine configuration files
Q11. Will Linux Kernel customization be covered?
Answer:
Yes.
Participants learn how to:
Configure the Linux kernel
Build the kernel using Yocto
Apply kernel patches
Update kernel configuration fragments
Extend kernel recipes
Q12. Will U-Boot customization be covered?
Answer:
Yes.
The course includes:
U-Boot configuration
U-Boot building
Bootloader customization
Bootloader patching
Environment configuration
Board recovery techniques
Q13. Will Root Filesystem customization be covered?
Answer:
Yes.
Participants learn how to:
Build custom root filesystems
Add and remove packages
Include custom applications
Configure init systems
Create production-ready RootFS images
Q14. Will boot flow be covered?
Answer:
Yes.
The course explains the complete Embedded Linux boot sequence, including:
ROM
Bootloader
U-Boot
Linux Kernel
Root Filesystem
using practical demonstrations on BeagleBone Black.
Q15. Is this a hands-on course?
Answer:
Yes.
Every theoretical topic is accompanied by practical laboratory exercises, allowing participants to immediately apply what they learn using real Embedded Linux hardware and Yocto build environments.
Q16. What tools and technologies are used?
Answer:
Participants work with:
Yocto Project
BitBake
Poky
OpenEmbedded
Linux Kernel
U-Boot
BSP Layers
Recipes
RootFS
BeagleBone Black
Q17. What practical exercises are included?
Answer:
Participants will:
Build Embedded Linux images
Configure and build Linux kernels
Configure U-Boot
Create Yocto meta layers
Write BitBake recipes
Extend recipes using bbappend
Create BSP layers
Build and test systems on hardware
Customize RootFS
Apply kernel and bootloader patches
Q18. What skills will I gain?
Answer:
After completing the course, participants should be able to:
Build complete Embedded Linux systems using Yocto
Configure and customize Linux kernels
Create and manage Yocto layers
Develop BitBake recipes
Customize BSPs
Configure bootloaders
Generate custom root filesystems
Build production-ready Embedded Linux images
Q19. How is this course different from Embedded Linux System Internals?
Answer:
Embedded Linux System Internals focuses on understanding how Linux works internally, including boot flow, kernel architecture, device drivers, interrupts, synchronization, DMA, and debugging.
Embedded Linux with Yocto builds on those foundations by teaching engineers how to use the Yocto Project to create, customize, package, and maintain complete Embedded Linux distributions for production hardware.
Q20. What makes this course different?
Answer:
This training emphasizes practical Embedded Linux product development through:
Deep Yocto Project knowledge
Extensive BitBake practice
Real hardware labs using BeagleBone Black
BSP customization
Kernel and bootloader patching
Production-oriented RootFS customization
Instructor-led guidance from an experienced Embedded Linux consultant
Q21. Do I need my own Linux machine?
Answer:
Yes.
Participants should have a PC or virtual machine running Ubuntu 22.04 or later, with approximately 150 GB of free disk space and 8 GB RAM to support Yocto builds.
Q22. What will I be able to do after this course?
Answer:
After completing this course, participants should be able to:
Build Embedded Linux systems using the Yocto Project
Develop and customize Yocto layers
Configure and patch U-Boot and Linux kernels
Create production-ready root filesystems
Add custom applications and packages
Customize BSPs for new hardware platforms
Build and maintain Embedded Linux distributions for commercial products
