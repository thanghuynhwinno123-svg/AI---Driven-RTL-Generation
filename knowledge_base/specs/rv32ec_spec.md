# RV32EC_Zmmul Tiny MCU Core Specification (v0.2-draft)

## 1. Scope

Defines a Cortex-M0-class microcontroller CPU core based on RISC-V for low-cost, low-power embedded systems.

## 2. Architecture Overview

* Core type: 32-bit in-order MCU CPU
* Core ISA: `RV32EC_Zmmul`
* Privilege levels: `M-mode` only
* Pipeline: 3-stage (`Fetch`, `Decode`, `Execute`)
* Issue: Single-issue
* Branch prediction: Not implemented
* MMU/FPU: Not implemented

## 3. ISA Support

* Mandatory:
  * `RV32E`
  * `C` (compressed instructions)
  * `Zmmul` (multiply-only signed integer multiply subset)
* Multiply/Divide behavior:
  * Multiply instructions supported
  * Divide/remainder instructions not supported
* Unsupported opcode policy:
  * Unsupported/illegal opcodes shall raise an illegal-instruction trap

## 4. Interrupt and Exception Model

* RISC-V machine-level trap handling
* Direct trap mode only: `mtvec.MODE=0`
* Vectored mode: Not supported
* Single external interrupt input (no priority/preemption model)
* Interrupt entry latency target: 12-20 cycles (implementation dependent)

## 5. Bus and Memory Interface

* Instruction fetch path:
  * Primary execution memory is internal IRAM/SRAM
  * External SPI ROM is used as boot image source in SPI boot mode
* Data access path: implementation-defined
* Peripheral integration: APB via integrated APB bridge
* Memory sizes (default configuration):
  * Boot ROM (internal): up to 16 KB
  * External SPI ROM (boot image storage): implementation-defined (any size)
  * IRAM (internal instruction RAM): 16 KB
  * DRAM (internal data RAM): 8 KB

## 6. Boot and Programming

* Debug block: Not included
* Boot ROM: Included
* Bootloader interface: SPI (for SPI boot mode)
* Boot modes:
  1. ROM boot mode:
     * Reset enters ROM
     * Execution remains in ROM image
     * Control transfers to ROM reset/application entry
  2. SPI to IRAM boot mode:
     * Reset enters ROM
     * ROM performs minimal initialization and SPI setup
     * ROM reads boot image header from external SPI ROM:
       * Program start address
       * Program size
     * Program size must be less than 16 KB
     * ROM loads image from external SPI ROM into IRAM/SRAM execution memory
     * Optional image validation (policy is SoC/product-defined)
     * On boot failure (e.g., SPI read/validation failure), system enters fail-stop state

## 7. Toolchain

* GCC RISC-V support (bare-metal)

## 8. Design Targets (Non-Normative)

* Performance/area class: Cortex-M0-class
* Priorities: low gate count, deterministic behavior, low power

## 9. Configurability

* Multiply microarchitecture choice (e.g., iterative vs optimized multi-cycle)
* SoC-defined APB map and memory sizing

## 10. Open Items

* Final APB memory map and peripheral slot allocation
* Reset/application vector addresses
* Boot mode select mechanism (e.g., strap pin or configuration fuse)
* Instruction/data interface timing and handshake details
* Instruction-class cycle timing table
* Sleep/deep-sleep behavior and `WFI` policy
* Boot image validation policy details (if enabled)
