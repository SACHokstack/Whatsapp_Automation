# Course Overview

Linux Kernel Programming is a 2-day hands-on course (14 hours) for engineers who want to understand how Linux device drivers work inside the kernel. It covers kernel space concepts, kernel module development, character driver implementation, user–kernel communication, interrupt handling, and deferred work mechanisms. HRDC Registration No: 10001697764.

Held at Timmins Training Center, Penang on 20–21 August 2026.

Who should attend:
- Embedded software engineers
- Firmware engineers
- Linux system developers
- Device driver developers
- Engineers working with embedded Linux platforms

Target industries: Embedded Systems and IoT, Semiconductor and Electronics, Automotive and Industrial Automation, Telecom and Networking Equipment, Consumer Electronics and Device Manufacturing.

Prerequisites:
- Comfortable with Embedded C programming
- Basic Linux command-line knowledge
- Familiarity with Linux fundamentals
- Prior exposure to embedded Linux is helpful

Course structure (4 sessions, 2 days):

Day 1:
- Session 1: Kernel Space Fundamentals — driver concepts, Linux driver ecosystem, kernel source organization; hands-on: configure and build the kernel
- Session 2: Kernel Module Development — kernel modules and their need, loading/unloading/stacking modules; hands-on: write a simple kernel module, set up the build system

Day 2:
- Session 3: Character Driver Development — character special device files, major & minor numbers, complete user space to kernel space flow, IOCTL, udev and automatic device file creation; hands-on: write a character driver, exchange data with user space, auto-create device files
- Session 4: Interrupt Handling & Deferred Work — interrupt basics, IRQ numbers, registering interrupt handlers, SoftIRQ, bottom halves using tasklets and work queues; hands-on: write an interrupt handler driver, register tasklet and work queue as bottom halves

Key topics:
- How Linux device drivers work inside the kernel
- Kernel space vs user space distinction
- Writing, compiling, loading, and unloading kernel modules
- Character driver: open, read, write, ioctl file operations
- Device file management with udev
- Safe data transfer between user space and kernel space
- Interrupt-driven driver design with deferred processing

Tools used: Linux Kernel, Kernel Modules, Character Drivers, GCC, Makefile, dmesg, udev, IRQ, Tasklets, Work Queues.

Practical outcomes:
- Build and manage basic Linux kernel modules confidently
- Write simple character drivers and expose device files to user space
- Exchange data safely between user applications and kernel drivers
- Implement interrupt handling and deferred work mechanisms

Investment: RM 3,000 per participant. Group discount available for minimum 3 pax. HRDC claimable.

SECTION X – LINUX KERNEL PROGRAMMING KNOWLEDGE BASE
Q1. What is this course about?
Answer:
Linux Kernel Programming is a 2-day hands-on training that teaches engineers how to develop Linux kernel modules and device drivers.
Participants learn how Linux drivers work inside the kernel, build kernel modules, develop character drivers, exchange data between user space and kernel space, and implement interrupt-driven driver behavior using practical coding exercises.
Q2. Who should attend?
Answer:
This course is suitable for:
Embedded Software Engineers
Firmware Engineers
Linux System Developers
Device Driver Developers
Engineers working with Embedded Linux platforms
Q3. Is this suitable for beginners?
Answer:
This is an intermediate-level course.
Participants should already be comfortable with Embedded C programming and basic Linux command-line usage. Prior exposure to Embedded Linux is helpful but not mandatory.
Q4. What are the prerequisites?
Answer:
Participants should have:
Good knowledge of Embedded C
Comfortable working in a Linux environment
Basic Linux command-line knowledge
Familiarity with Linux fundamentals
Prior experience with Embedded Linux is recommended.
Q5. What is Linux Kernel Programming?
Answer:
Linux Kernel Programming involves developing software that runs inside the Linux kernel rather than user space.
This includes creating device drivers, handling hardware events, managing kernel modules, and enabling communication between applications and hardware devices.
Q6. Will Linux kernel modules be covered?
Answer:
Yes.
Participants learn how to:
Build kernel modules
Compile kernel modules
Load modules
Unload modules
Test modules
Manage module dependencies
through guided hands-on exercises.
Q7. Will character driver development be covered?
Answer:
Yes.
Participants learn how to:
Write character drivers
Create device files
Register major and minor numbers
Implement file operations
Build complete character drivers from scratch
Q8. Will user-kernel communication be covered?
Answer:
Yes.
The course teaches how applications communicate safely with kernel drivers, including:
Data exchange
Device interfaces
User-space applications
IOCTL operations
Read and write operations
Q9. Will interrupt handling be covered?
Answer:
Yes.
Participants learn how to:
Register interrupt handlers
Handle IRQs
Write Interrupt Service Routines (ISR)
Implement interrupt-driven drivers
using practical coding exercises.
Q10. Will tasklets and work queues be covered?
Answer:
Yes.
The course covers Linux deferred work mechanisms, including:
Soft IRQ
Tasklets
Work queues
Bottom-half processing
Participants implement these mechanisms through practical labs.
Q11. Will device file management be covered?
Answer:
Yes.
Participants learn how to:
Create device files
Use udev
Automatically generate device nodes
Test driver functionality
Q12. Is this a hands-on course?
Answer:
Yes.
The course is highly practical and includes:
Kernel module development
Character driver programming
Driver testing
Interrupt handling
User-space application development
Device file management
Every major concept is reinforced through guided laboratory exercises.
Q13. What practical exercises are included?
Answer:
Participants will:
Configure and build the Linux kernel
Write a simple kernel module
Load and unload modules
Build and test a character driver
Exchange data between user space and kernel space
Automatically create device files using udev
Implement interrupt handlers
Register tasklets
Register work queues
Q14. What tools and technologies are used?
Answer:
Participants work with:
Linux Kernel
Kernel Modules
Character Drivers
GCC
Makefile
dmesg
udev
IRQ
Tasklets
Work Queues
Q15. What skills will I gain?
Answer:
After completing the course, participants should be able to:
Develop Linux kernel modules
Build character drivers
Manage device files
Exchange data between user space and kernel space
Implement interrupt-driven drivers
Apply deferred work mechanisms
Test and validate Linux drivers
Understand the Linux driver ecosystem
Q16. How is this course different from Embedded Linux System Internals?
Answer:
Embedded Linux System Internals focuses on understanding how the Linux operating system works, including boot flow, kernel architecture, and system internals.
Linux Kernel Programming focuses specifically on writing software that runs inside the Linux kernel, including kernel modules, character drivers, user-kernel communication, and interrupt-driven device drivers.
Q17. What makes this course different?
Answer:
This training emphasizes practical Linux driver development through:
Kernel-space programming
Hands-on kernel module development
Character driver implementation
User-kernel communication
Interrupt-driven programming
Real embedded Linux development scenarios
Participants gain practical coding experience that prepares them for embedded Linux driver development projects.
Q18. Is this relevant for Embedded Linux engineers?
Answer:
Yes.
Linux Kernel Programming is one of the core skills for engineers developing Embedded Linux platforms.
It is particularly valuable for engineers involved in:
BSP development
Device driver development
Hardware bring-up
Platform enablement
Embedded Linux product development
Q19. What laptop setup is required?
Answer:
Participants should prepare:
Software
Linux operating system
GCC compiler
Make
Linux kernel source
Build tools
dmesg
udev
Knowledge
Embedded C programming
Linux command-line usage
The instructor will guide participants through the kernel module build environment during the course.
Q20. What will I be able to do after this course?
Answer:
After completing this course, participants should be able to:
Build and manage Linux kernel modules
Develop basic Linux character drivers
Create and manage Linux device files
Implement user-kernel communication
Write interrupt-driven drivers
Use tasklets and work queues for deferred processing
Understand the architecture of Linux device drivers
Contribute to Embedded Linux kernel and driver development projects
These skills are directly applicable to engineers working in semiconductor, embedded systems, IoT, automotive, telecommunications, consumer electronics, and industrial automation.
