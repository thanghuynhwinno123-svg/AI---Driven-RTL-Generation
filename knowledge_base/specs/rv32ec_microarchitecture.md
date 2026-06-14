
# RV32EC_Zmmul Microarchitecture (Detailed)

## 1. Overview

This document defines a concrete microarchitectural blueprint for implementing the `RV32EC_Zmmul` core.

Target characteristics:

* 3-stage single-issue in-order pipeline
* Small control logic footprint
* Deterministic trap/interrupt handling
* Configurable multiply unit implementation

Top-level blocks:

* Fetch Unit (FU)
* Decode/Control Unit (DU)
* Execute/Writeback Unit (EX/WB)
* Integer Register File (16 x 32)
* CSR/Trap Controller
* Load/Store Interface
* Boot ROM Loader Assist Logic (for SPI-to-IRAM mode)

## 2. Pipeline Structure

Stages:

1. `F` (Fetch)
2. `D` (Decode)
3. `X` (Execute + memory access + writeback)

Pipeline goals:

* One instruction issued per cycle in no-stall path
* Global stall and flush controls for hazards and traps

## 3. Fetch Unit

### 3.1 Responsibilities

* Maintain `PC` and next-PC generation
* Fetch 16/32-bit instruction stream
* Handle compressed instruction boundaries
* Redirect fetch on branch/jump/trap

### 3.2 PC Selection Priority (highest first)

1. Trap/interrupt redirect (`mtvec`)
2. Exception return (`mepc` via `MRET`)
3. Taken branch/jump target from `X`
4. Sequential next PC (`PC + 2` or `PC + 4`)

### 3.3 Instruction Buffering

Recommended minimal prefetch structure:

* 32-bit fetch register + halfword align control
* Valid bit and replay support on stalls

## 4. Decode and Control Unit

### 4.1 Responsibilities

* Decode `RV32E`, `C`, `Zmmul` opcodes
* Generate control signals for EX datapath and writeback
* Classify instruction type: ALU, branch/jump, load/store, CSR, multiply, trap-return
* Detect illegal instruction patterns

### 4.2 Immediate Generation

* Support all required immediate forms for RV32E/C instructions in scope
* Sign-extension performed in decode path

### 4.3 Register Read

* Two read ports from register file (or equivalent time-multiplexed implementation)
* Read in `D`, consume in `X`

## 5. Execute/Writeback Unit

### 5.1 ALU Subsystem

Required operations:

* Add/subtract
* Logical ops (and/or/xor)
* Shifts
* Compare/set operations
* Branch target calculation and condition evaluation

### 5.2 Branch and Jump Handling

* Branch resolution in `X`
* On taken control transfer, flush younger instructions (`F`/`D`) and redirect `PC`
* No branch prediction

### 5.3 Multiply Unit (`Zmmul`)

Configurable implementation options:

* Iterative multiplier (small area, higher latency)
* Multi-cycle optimized multiplier (moderate area/performance tradeoff)

Control requirements:

* `mul_busy` handshake to pipeline control
* Stall front-end while multiply result is pending
* Commit writeback when multiply completes

### 5.4 Divide/Remainder Handling

* No divide/remainder datapath
* Decode of unsupported divide/remainder opcodes triggers illegal-instruction exception path

### 5.5 Writeback

* Single architectural writeback point at end of `X`
* Writeback sources:
  * ALU result
  * Multiply result
  * Load data
  * CSR read result (if applicable)

## 6. Load/Store Path

### 6.1 Responsibilities

* Effective address calculation in `X`
* Issue memory transaction for load/store
* Return load data for register writeback

### 6.2 Memory Wait-State Behavior

* Variable-latency memory supported via stall/ready handshake
* During pending memory response:
  * `X` stage holds instruction context
  * `F` and `D` are stalled

### 6.3 Alignment and Fault Policy

Implementation must define and keep consistent:

* Whether misaligned load/store traps are generated
* Whether subword accesses are supported natively or emulated

## 7. Hazard Management

### 7.1 Data Hazards

Allowed strategies:

* Minimal bypass network + interlock
* Full bypass for common ALU dependencies

Minimum correctness requirement:

* RAW hazards must never produce stale operand consumption

### 7.2 Structural Hazards

Potential shared resources:

* Register file port contention
* Memory path contention in compact implementations

Hazard controller must stall issue when structural conflict is present.

### 7.3 Control Hazards

* Branch/jump resolved in `X`
* Trap redirect has highest priority and flushes younger instructions

## 8. CSR and Trap Controller

### 8.1 CSR Access Pipeline

* CSR reads/writes decoded in `D`, executed in `X`
* Invalid CSR access triggers illegal-instruction trap

### 8.2 Trap Prioritization (recommended)

Within a cycle, recommended priority:

1. Synchronous exception from current instruction
2. Pending interrupt (if globally/local enabled)

### 8.3 Trap Side Effects

On trap entry:

* Capture faulting/interrupted PC into `mepc`
* Write cause into `mcause`
* Write detail into `mtval` (if available)
* Redirect `PC` to `mtvec.BASE`

## 9. Interrupt Handling Microflow

* External interrupt sampled and synchronized
* Pending reflected in `mip`
* Acceptance point occurs at instruction boundary
* On acceptance, younger pipeline stages are flushed and handler starts at `mtvec`

Latency target:

* 12-20 cycles from sampled pending condition to first handler fetch (implementation dependent)

## 10. Boot ROM Loader Microflow (SPI-to-IRAM)

State machine (conceptual):

1. `BOOT_RESET_ENTRY`
2. `BOOT_CLK_SPI_INIT`
3. `BOOT_READ_HEADER`
4. `BOOT_CHECK_HEADER`
5. `BOOT_COPY_PAYLOAD`
6. `BOOT_OPTIONAL_VALIDATE`
7. `BOOT_JUMP_APP`
8. `BOOT_FAIL_STOP`

Header checks:

* Valid magic/version
* `program_size < 16 KB`
* Start address in allowed execution region

Failure conditions (any -> fail-stop):

* SPI transaction timeout/error
* Header malformed
* Size/range violation
* Validation failure (if enabled)

## 11. Reset and Initialization Behavior

On reset deassertion:

* `PC` <- reset vector (Boot ROM)
* Integer registers unspecified unless explicitly zeroed by ROM/startup
* CSRs initialized to defined reset values
* Pipeline valid bits cleared
* Outstanding memory operations cancelled/invalidated

## 12. Clocking and Power Intent (Microarchitectural)

* Clock-gating points may include:
  * Multiplier when idle
  * Fetch pipeline when globally stalled
  * Optional CSR/counter blocks when disabled
* `WFI` behavior should quiesce front-end until wake condition

## 13. Configurable Parameters

Recommended synthesis-time parameters:

* `MUL_IMPL` (`ITERATIVE`, `MCYCLE_OPT`)
* `IRAM_BYTES` (default 16384)
* `DRAM_BYTES` (default 8192)
* `BOOT_ROM_BYTES_MAX` (16384)
* `ENABLE_IMAGE_VALIDATION` (0/1)
* `TRAP_ON_MISALIGNED` (0/1)

## 14. Verification Guidance

Minimum verification matrix:

* ISA compliance subset (`RV32E`, `C`, `Zmmul`)
* Illegal instruction trapping (including divide/rem opcodes)
* Branch flush correctness under back-to-back control transfers
* Interrupt entry/exit correctness (`mepc/mcause/mstatus` effects)
* SPI-to-IRAM boot success/failure paths
* Fail-stop terminal behavior
* Memory wait-state robustness (load/store stalls)

Recommended methods:

* Directed tests for boot/trap edge cases
* Constrained-random instruction streams with reference model comparison
* Assertions for stall/flush mutual exclusion and single-commit invariants
