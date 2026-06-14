
# RV32EC_Zmmul ISA Definition

## 1. Profile

Supported ISA profile:

* `RV32E`
* `C`
* `Zmmul`

Execution/trap policy:

* Machine mode only (`M-mode`)
* Unsupported/illegal opcode -> illegal-instruction trap
* Divide/remainder instructions are not supported

Register model:

* Architectural integer registers: `x0..x15`
* Any encoding that names `x16..x31` is illegal for this core profile

## 2. Instruction Encoding Fields

## 2.1 32-bit Base Formats

* `R-type`: `funct7[31:25] | rs2[24:20] | rs1[19:15] | funct3[14:12] | rd[11:7] | opcode[6:0]`
* `I-type`: `imm[31:20] | rs1[19:15] | funct3[14:12] | rd[11:7] | opcode[6:0]`
* `S-type`: `imm[11:5][31:25] | rs2[24:20] | rs1[19:15] | funct3[14:12] | imm[4:0][11:7] | opcode[6:0]`
* `B-type`: `imm[12|10:5][31:25] | rs2[24:20] | rs1[19:15] | funct3[14:12] | imm[4:1|11][11:7] | opcode[6:0]`
* `U-type`: `imm[31:12] | rd[11:7] | opcode[6:0]`
* `J-type`: `imm[20|10:1|11|19:12] | rd[11:7] | opcode[6:0]`

## 2.2 16-bit Compressed Formats

* `CR`: `funct4[15:12] | rd/rs1[11:7] | rs2[6:2] | op[1:0]`
* `CI`: `funct3[15:13] | imm[12] | rd/rs1[11:7] | imm[6:2] | op[1:0]`
* `CSS`: `funct3[15:13] | imm[12:7] | rs2[6:2] | op[1:0]`
* `CIW`: `funct3[15:13] | imm[12:5] | rd'[4:2] | op[1:0]`
* `CL`: `funct3[15:13] | imm[5:3] | rs1'[9:7] | imm[2|6] | rd'[4:2] | op[1:0]`
* `CS`: `funct3[15:13] | imm[5:3] | rs1'[9:7] | imm[2|6] | rs2'[4:2] | op[1:0]`
* `CA`: `funct6[15:10] | rd'/rs1'[9:7] | funct2[6:5] | rs2'[4:2] | op[1:0]`
* `CB`: `funct3[15:13] | offset/imm bits | rs1'[9:7] | offset/imm bits | op[1:0]`
* `CJ`: `funct3[15:13] | jump offset bits[12:2] | op[1:0]`

Notes:

* `rd'`, `rs1'`, `rs2'` map to compact register set `x8..x15`
* Immediate bit packing follows the standard RISC-V C extension mapping

## 2.3 ASCII Bitfield Diagrams (All Formats)

### 32-bit Formats

`R-type`

```text
31            25 24    20 19    15 14   12 11     7 6      0
+---------------+--------+--------+--------+--------+--------+
|    funct7     |  rs2   |  rs1   | funct3 |   rd   | opcode |
+---------------+--------+--------+--------+--------+--------+
```

`I-type`

```text
31                         20 19    15 14   12 11     7 6      0
+---------------------------+--------+--------+--------+--------+
|          imm[11:0]        |  rs1   | funct3 |   rd   | opcode |
+---------------------------+--------+--------+--------+--------+
```

`S-type`

```text
31            25 24    20 19    15 14   12 11     7 6      0
+---------------+--------+--------+--------+--------+--------+
|   imm[11:5]   |  rs2   |  rs1   | funct3 | imm[4:0]| opcode |
+---------------+--------+--------+--------+--------+--------+
```

`B-type`

```text
31          25 24    20 19    15 14   12 11        7 6      0
+-------------+--------+--------+--------+-----------+--------+
|imm[12|10:5] |  rs2   |  rs1   | funct3 |imm[4:1|11]| opcode |
+-------------+--------+--------+--------+-----------+--------+
```

`U-type`

```text
31                                      12 11     7 6      0
+----------------------------------------+--------+--------+
|               imm[31:12]               |   rd   | opcode |
+----------------------------------------+--------+--------+
```

`J-type`

```text
31                                   12 11     7 6      0
+-------------------------------------+--------+--------+
|         imm[20|10:1|11|19:12]       |   rd   | opcode |
+-------------------------------------+--------+--------+
```

### 16-bit Compressed Formats

`CR`

```text
15       12 11        7 6         2 1    0
+----------+-----------+-----------+------+
|  funct4  |  rd/rs1   |    rs2    |  op  |
+----------+-----------+-----------+------+
```

`CI`

```text
15     13 12 11        7 6         2 1    0
+--------+---+-----------+-----------+------+
| funct3 |imm|  rd/rs1   | imm[4:0]  |  op  |
+--------+---+-----------+-----------+------+
```

`CSS`

```text
15     13 12                 7 6         2 1    0
+--------+--------------------+-----------+------+
| funct3 |      imm[5:0]      |    rs2    |  op  |
+--------+--------------------+-----------+------+
```

`CIW`

```text
15     13 12                      5 4     2 1    0
+--------+-------------------------+-------+------+
| funct3 |        nzuimm[7:0]      |  rd'  |  op  |
+--------+-------------------------+-------+------+
```

`CL`

```text
15     13 12   10 9      7 6   5 4      2 1    0
+--------+-------+---------+------+--------+------+
| funct3 | uimm  |  rs1'   |uimm  |  rd'   |  op  |
+--------+-------+---------+------+--------+------+
```

`CS`

```text
15     13 12   10 9      7 6   5 4      2 1    0
+--------+-------+---------+------+--------+------+
| funct3 | uimm  |  rs1'   |uimm  |  rs2'  |  op  |
+--------+-------+---------+------+--------+------+
```

`CA`

```text
15        10 9      7 6    5 4      2 1    0
+-----------+---------+------+--------+------+
|  funct6   | rd'/rs1'|funct2|  rs2'  |  op  |
+-----------+---------+------+--------+------+
```

`CB`

```text
15     13 12      10 9      7 6       2 1    0
+--------+----------+---------+---------+------+
| funct3 | imm/offs |  rs1'   | imm/offs|  op  |
+--------+----------+---------+---------+------+
```

`CJ`

```text
15     13 12                           2 1    0
+--------+------------------------------+------+
| funct3 |        jump_offset           |  op  |
+--------+------------------------------+------+
```

## 3. Supported 32-bit Instructions

## 3.1 Integer Base (`RV32E`)

<table><tbody><tr><th><p>Instruction</p></th><th><p>Format</p></th><th><p>Key fixed fields</p></th><th><p>Operand fields</p></th></tr><tr><td><p><code>LUI</code></p></td><td><p>U</p></td><td><p><code>opcode=0110111</code></p></td><td><p><code>rd, imm[31:12]</code></p></td></tr><tr><td><p><code>AUIPC</code></p></td><td><p>U</p></td><td><p><code>opcode=0010111</code></p></td><td><p><code>rd, imm[31:12]</code></p></td></tr><tr><td><p><code>JAL</code></p></td><td><p>J</p></td><td><p><code>opcode=1101111</code></p></td><td><p><code>rd, imm[20:1]</code></p></td></tr><tr><td><p><code>JALR</code></p></td><td><p>I</p></td><td><p><code>opcode=1100111, funct3=000</code></p></td><td><p><code>rd, rs1, imm[11:0]</code></p></td></tr><tr><td><p><code>BEQ</code></p></td><td><p>B</p></td><td><p><code>opcode=1100011, funct3=000</code></p></td><td><p><code>rs1, rs2, imm[12:1]</code></p></td></tr><tr><td><p><code>BNE</code></p></td><td><p>B</p></td><td><p><code>opcode=1100011, funct3=001</code></p></td><td><p><code>rs1, rs2, imm[12:1]</code></p></td></tr><tr><td><p><code>BLT</code></p></td><td><p>B</p></td><td><p><code>opcode=1100011, funct3=100</code></p></td><td><p><code>rs1, rs2, imm[12:1]</code></p></td></tr><tr><td><p><code>BGE</code></p></td><td><p>B</p></td><td><p><code>opcode=1100011, funct3=101</code></p></td><td><p><code>rs1, rs2, imm[12:1]</code></p></td></tr><tr><td><p><code>BLTU</code></p></td><td><p>B</p></td><td><p><code>opcode=1100011, funct3=110</code></p></td><td><p><code>rs1, rs2, imm[12:1]</code></p></td></tr><tr><td><p><code>BGEU</code></p></td><td><p>B</p></td><td><p><code>opcode=1100011, funct3=111</code></p></td><td><p><code>rs1, rs2, imm[12:1]</code></p></td></tr><tr><td><p><code>LB</code></p></td><td><p>I</p></td><td><p><code>opcode=0000011, funct3=000</code></p></td><td><p><code>rd, rs1, imm[11:0]</code></p></td></tr><tr><td><p><code>LH</code></p></td><td><p>I</p></td><td><p><code>opcode=0000011, funct3=001</code></p></td><td><p><code>rd, rs1, imm[11:0]</code></p></td></tr><tr><td><p><code>LW</code></p></td><td><p>I</p></td><td><p><code>opcode=0000011, funct3=010</code></p></td><td><p><code>rd, rs1, imm[11:0]</code></p></td></tr><tr><td><p><code>LBU</code></p></td><td><p>I</p></td><td><p><code>opcode=0000011, funct3=100</code></p></td><td><p><code>rd, rs1, imm[11:0]</code></p></td></tr><tr><td><p><code>LHU</code></p></td><td><p>I</p></td><td><p><code>opcode=0000011, funct3=101</code></p></td><td><p><code>rd, rs1, imm[11:0]</code></p></td></tr><tr><td><p><code>SB</code></p></td><td><p>S</p></td><td><p><code>opcode=0100011, funct3=000</code></p></td><td><p><code>rs1, rs2, imm[11:0]</code></p></td></tr><tr><td><p><code>SH</code></p></td><td><p>S</p></td><td><p><code>opcode=0100011, funct3=001</code></p></td><td><p><code>rs1, rs2, imm[11:0]</code></p></td></tr><tr><td><p><code>SW</code></p></td><td><p>S</p></td><td><p><code>opcode=0100011, funct3=010</code></p></td><td><p><code>rs1, rs2, imm[11:0]</code></p></td></tr><tr><td><p><code>ADDI</code></p></td><td><p>I</p></td><td><p><code>opcode=0010011, funct3=000</code></p></td><td><p><code>rd, rs1, imm[11:0]</code></p></td></tr><tr><td><p><code>SLTI</code></p></td><td><p>I</p></td><td><p><code>opcode=0010011, funct3=010</code></p></td><td><p><code>rd, rs1, imm[11:0]</code></p></td></tr><tr><td><p><code>SLTIU</code></p></td><td><p>I</p></td><td><p><code>opcode=0010011, funct3=011</code></p></td><td><p><code>rd, rs1, imm[11:0]</code></p></td></tr><tr><td><p><code>XORI</code></p></td><td><p>I</p></td><td><p><code>opcode=0010011, funct3=100</code></p></td><td><p><code>rd, rs1, imm[11:0]</code></p></td></tr><tr><td><p><code>ORI</code></p></td><td><p>I</p></td><td><p><code>opcode=0010011, funct3=110</code></p></td><td><p><code>rd, rs1, imm[11:0]</code></p></td></tr><tr><td><p><code>ANDI</code></p></td><td><p>I</p></td><td><p><code>opcode=0010011, funct3=111</code></p></td><td><p><code>rd, rs1, imm[11:0]</code></p></td></tr><tr><td><p><code>SLLI</code></p></td><td><p>I</p></td><td><p><code>opcode=0010011, funct3=001, funct7=0000000</code></p></td><td><p><code>rd, rs1, shamt[4:0]</code></p></td></tr><tr><td><p><code>SRLI</code></p></td><td><p>I</p></td><td><p><code>opcode=0010011, funct3=101, funct7=0000000</code></p></td><td><p><code>rd, rs1, shamt[4:0]</code></p></td></tr><tr><td><p><code>SRAI</code></p></td><td><p>I</p></td><td><p><code>opcode=0010011, funct3=101, funct7=0100000</code></p></td><td><p><code>rd, rs1, shamt[4:0]</code></p></td></tr><tr><td><p><code>ADD</code></p></td><td><p>R</p></td><td><p><code>opcode=0110011, funct3=000, funct7=0000000</code></p></td><td><p><code>rd, rs1, rs2</code></p></td></tr><tr><td><p><code>SUB</code></p></td><td><p>R</p></td><td><p><code>opcode=0110011, funct3=000, funct7=0100000</code></p></td><td><p><code>rd, rs1, rs2</code></p></td></tr><tr><td><p><code>SLL</code></p></td><td><p>R</p></td><td><p><code>opcode=0110011, funct3=001, funct7=0000000</code></p></td><td><p><code>rd, rs1, rs2</code></p></td></tr><tr><td><p><code>SLT</code></p></td><td><p>R</p></td><td><p><code>opcode=0110011, funct3=010, funct7=0000000</code></p></td><td><p><code>rd, rs1, rs2</code></p></td></tr><tr><td><p><code>SLTU</code></p></td><td><p>R</p></td><td><p><code>opcode=0110011, funct3=011, funct7=0000000</code></p></td><td><p><code>rd, rs1, rs2</code></p></td></tr><tr><td><p><code>XOR</code></p></td><td><p>R</p></td><td><p><code>opcode=0110011, funct3=100, funct7=0000000</code></p></td><td><p><code>rd, rs1, rs2</code></p></td></tr><tr><td><p><code>SRL</code></p></td><td><p>R</p></td><td><p><code>opcode=0110011, funct3=101, funct7=0000000</code></p></td><td><p><code>rd, rs1, rs2</code></p></td></tr><tr><td><p><code>SRA</code></p></td><td><p>R</p></td><td><p><code>opcode=0110011, funct3=101, funct7=0100000</code></p></td><td><p><code>rd, rs1, rs2</code></p></td></tr><tr><td><p><code>OR</code></p></td><td><p>R</p></td><td><p><code>opcode=0110011, funct3=110, funct7=0000000</code></p></td><td><p><code>rd, rs1, rs2</code></p></td></tr><tr><td><p><code>AND</code></p></td><td><p>R</p></td><td><p><code>opcode=0110011, funct3=111, funct7=0000000</code></p></td><td><p><code>rd, rs1, rs2</code></p></td></tr><tr><td><p><code>FENCE</code></p></td><td><p>I</p></td><td><p><code>opcode=0001111, funct3=000</code></p></td><td><p><code>pred, succ, fm, rs1(=x0), rd(=x0)</code></p></td></tr><tr><td><p><code>ECALL</code></p></td><td><p>I</p></td><td><p><code>opcode=1110011, funct3=000, imm=000000000000</code></p></td><td><p>none</p></td></tr><tr><td><p><code>EBREAK</code></p></td><td><p>I</p></td><td><p><code>opcode=1110011, funct3=000, imm=000000000001</code></p></td><td><p>none</p></td></tr></tbody></table>

## 3.2 CSR/System Instructions (Machine-level runtime)

<table><tbody><tr><th><p>Instruction</p></th><th><p>Format</p></th><th><p>Key fixed fields</p></th><th><p>Operand fields</p></th></tr><tr><td><p><code>CSRRW</code></p></td><td><p>I</p></td><td><p><code>opcode=1110011, funct3=001</code></p></td><td><p><code>rd, rs1, csr[11:0]</code></p></td></tr><tr><td><p><code>CSRRS</code></p></td><td><p>I</p></td><td><p><code>opcode=1110011, funct3=010</code></p></td><td><p><code>rd, rs1, csr[11:0]</code></p></td></tr><tr><td><p><code>CSRRC</code></p></td><td><p>I</p></td><td><p><code>opcode=1110011, funct3=011</code></p></td><td><p><code>rd, rs1, csr[11:0]</code></p></td></tr><tr><td><p><code>CSRRWI</code></p></td><td><p>I</p></td><td><p><code>opcode=1110011, funct3=101</code></p></td><td><p><code>rd, zimm[4:0], csr[11:0]</code></p></td></tr><tr><td><p><code>CSRRSI</code></p></td><td><p>I</p></td><td><p><code>opcode=1110011, funct3=110</code></p></td><td><p><code>rd, zimm[4:0], csr[11:0]</code></p></td></tr><tr><td><p><code>CSRRCI</code></p></td><td><p>I</p></td><td><p><code>opcode=1110011, funct3=111</code></p></td><td><p><code>rd, zimm[4:0], csr[11:0]</code></p></td></tr><tr><td><p><code>MRET</code></p></td><td><p>I</p></td><td><p><code>opcode=1110011, funct3=000, imm=001100000010</code></p></td><td><p>none</p></td></tr></tbody></table>

## 3.3 Integer Multiply (`Zmmul`)

<table><tbody><tr><th><p>Instruction</p></th><th><p>Format</p></th><th><p>Key fixed fields</p></th><th><p>Operand fields</p></th></tr><tr><td><p><code>MUL</code></p></td><td><p>R</p></td><td><p><code>opcode=0110011, funct7=0000001, funct3=000</code></p></td><td><p><code>rd, rs1, rs2</code></p></td></tr><tr><td><p><code>MULH</code></p></td><td><p>R</p></td><td><p><code>opcode=0110011, funct7=0000001, funct3=001</code></p></td><td><p><code>rd, rs1, rs2</code></p></td></tr><tr><td><p><code>MULHSU</code></p></td><td><p>R</p></td><td><p><code>opcode=0110011, funct7=0000001, funct3=010</code></p></td><td><p><code>rd, rs1, rs2</code></p></td></tr><tr><td><p><code>MULHU</code></p></td><td><p>R</p></td><td><p><code>opcode=0110011, funct7=0000001, funct3=011</code></p></td><td><p><code>rd, rs1, rs2</code></p></td></tr></tbody></table>

## 4. Supported 16-bit Compressed Instructions (`C`)

## 4.1 Stack-pointer and immediate forms

<table><tbody><tr><th><p>Instruction</p></th><th><p>Format</p></th><th><p>Quadrant (<code>op[1:0]</code>)</p></th><th><p>Key fields</p></th></tr><tr><td><p><code>C.ADDI4SPN</code></p></td><td><p>CIW</p></td><td><p><code>00</code></p></td><td><p><code>funct3=000, rd', nzuimm</code></p></td></tr><tr><td><p><code>C.ADDI</code></p></td><td><p>CI</p></td><td><p><code>01</code></p></td><td><p><code>funct3=000, rd/rs1, imm</code></p></td></tr><tr><td><p><code>C.NOP</code></p></td><td><p>CI</p></td><td><p><code>01</code></p></td><td><p><code>funct3=000, rd=x0, imm=0</code></p></td></tr><tr><td><p><code>C.LI</code></p></td><td><p>CI</p></td><td><p><code>01</code></p></td><td><p><code>funct3=010, rd, imm</code></p></td></tr><tr><td><p><code>C.LUI</code></p></td><td><p>CI</p></td><td><p><code>01</code></p></td><td><p><code>funct3=011, rd!=x0/x2, imm</code></p></td></tr><tr><td><p><code>C.ADDI16SP</code></p></td><td><p>CI</p></td><td><p><code>01</code></p></td><td><p><code>funct3=011, rd=x2, nzimm</code></p></td></tr><tr><td><p><code>C.SLLI</code></p></td><td><p>CI</p></td><td><p><code>10</code></p></td><td><p><code>funct3=000, rd/rs1, shamt</code></p></td></tr></tbody></table>

## 4.2 Loads/stores (compressed)

<table><tbody><tr><th><p>Instruction</p></th><th><p>Format</p></th><th><p>Quadrant (<code>op[1:0]</code>)</p></th><th><p>Key fields</p></th></tr><tr><td><p><code>C.LW</code></p></td><td><p>CL</p></td><td><p><code>00</code></p></td><td><p><code>funct3=010, rd', rs1', uimm</code></p></td></tr><tr><td><p><code>C.SW</code></p></td><td><p>CS</p></td><td><p><code>00</code></p></td><td><p><code>funct3=110, rs2', rs1', uimm</code></p></td></tr><tr><td><p><code>C.LWSP</code></p></td><td><p>CI</p></td><td><p><code>10</code></p></td><td><p><code>funct3=010, rd!=x0, uimm</code></p></td></tr><tr><td><p><code>C.SWSP</code></p></td><td><p>CSS</p></td><td><p><code>10</code></p></td><td><p><code>funct3=110, rs2, uimm</code></p></td></tr></tbody></table>

## 4.3 Control transfer (compressed)

<table><tbody><tr><th><p>Instruction</p></th><th><p>Format</p></th><th><p>Quadrant (<code>op[1:0]</code>)</p></th><th><p>Key fields</p></th></tr><tr><td><p><code>C.J</code></p></td><td><p>CJ</p></td><td><p><code>01</code></p></td><td><p><code>funct3=101, offset</code></p></td></tr><tr><td><p><code>C.JAL</code> (RV32 only)</p></td><td><p>CJ</p></td><td><p><code>01</code></p></td><td><p><code>funct3=001, offset</code></p></td></tr><tr><td><p><code>C.BEQZ</code></p></td><td><p>CB</p></td><td><p><code>01</code></p></td><td><p><code>funct3=110, rs1', offset</code></p></td></tr><tr><td><p><code>C.BNEZ</code></p></td><td><p>CB</p></td><td><p><code>01</code></p></td><td><p><code>funct3=111, rs1', offset</code></p></td></tr><tr><td><p><code>C.JR</code></p></td><td><p>CR</p></td><td><p><code>10</code></p></td><td><p><code>funct4=1000, rs1!=x0, rs2=x0</code></p></td></tr><tr><td><p><code>C.JALR</code></p></td><td><p>CR</p></td><td><p><code>10</code></p></td><td><p><code>funct4=1001, rs1!=x0, rs2=x0</code></p></td></tr></tbody></table>

## 4.4 Register-register ALU (compressed)

<table><tbody><tr><th><p>Instruction</p></th><th><p>Format</p></th><th><p>Quadrant (<code>op[1:0]</code>)</p></th><th><p>Key fields</p></th></tr><tr><td><p><code>C.MV</code></p></td><td><p>CR</p></td><td><p><code>10</code></p></td><td><p><code>funct4=1000, rd!=x0, rs2!=x0</code></p></td></tr><tr><td><p><code>C.ADD</code></p></td><td><p>CR</p></td><td><p><code>10</code></p></td><td><p><code>funct4=1001, rd/rs1!=x0, rs2!=x0</code></p></td></tr><tr><td><p><code>C.ANDI</code></p></td><td><p>CB</p></td><td><p><code>01</code></p></td><td><p><code>funct3=100, funct2=10, rs1', imm</code></p></td></tr><tr><td><p><code>C.SRLI</code></p></td><td><p>CB</p></td><td><p><code>01</code></p></td><td><p><code>funct3=100, funct2=00, rs1', shamt</code></p></td></tr><tr><td><p><code>C.SRAI</code></p></td><td><p>CB</p></td><td><p><code>01</code></p></td><td><p><code>funct3=100, funct2=01, rs1', shamt</code></p></td></tr><tr><td><p><code>C.SUB</code></p></td><td><p>CA</p></td><td><p><code>01</code></p></td><td><p><code>funct6=100011, funct2=00, rd'/rs1', rs2'</code></p></td></tr><tr><td><p><code>C.XOR</code></p></td><td><p>CA</p></td><td><p><code>01</code></p></td><td><p><code>funct6=100011, funct2=01, rd'/rs1', rs2'</code></p></td></tr><tr><td><p><code>C.OR</code></p></td><td><p>CA</p></td><td><p><code>01</code></p></td><td><p><code>funct6=100011, funct2=10, rd'/rs1', rs2'</code></p></td></tr><tr><td><p><code>C.AND</code></p></td><td><p>CA</p></td><td><p><code>01</code></p></td><td><p><code>funct6=100011, funct2=11, rd'/rs1', rs2'</code></p></td></tr></tbody></table>

## 4.5 Breakpoint

<table><tbody><tr><th><p>Instruction</p></th><th><p>Format</p></th><th><p>Quadrant (<code>op[1:0]</code>)</p></th><th><p>Key fields</p></th></tr><tr><td><p><code>C.EBREAK</code></p></td><td><p>CR</p></td><td><p><code>10</code></p></td><td><p><code>funct4=1001, rd/rs1=x0, rs2=x0</code></p></td></tr></tbody></table>

## 5. Explicitly Unsupported Instructions

* All divide/remainder instructions: `DIV`, `DIVU`, `REM`, `REMU`
* Any RV32I/RV32E encoding that references `x16..x31`
* Any extension opcode not listed in this file (`F/D/A/V/B/...`) unless added in a future revision

## 6. Quick Field Reference

* `rd`: destination register (5-bit encoding; legal values map to `x0..x15`)
* `rs1`, `rs2`: source registers
* `rd'`, `rs1'`, `rs2'`: compressed compact regs (`x8..x15`)
* `imm`: sign-extended immediate
* `uimm`: zero-extended immediate
* `shamt`: shift amount
* `csr`: CSR address field
* `zimm`: immediate used by CSR immediate forms
