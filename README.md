# SSI Framework
Code for the LIVE 2022 paper "System-Specific Interpreters Make Megasystems
Friendlier."

A system-specific interpreter is an interpreter specialized to execute and
trace individual modules of a larger system independently, without having to
build, run, and trace the overall system.

This repository is broken into two parts:
- An SSI framework, i.e., a flexible parser and interpreter for the C language.
- An SSI written on top of that framework for a Linux device driver.

## Running the Example SSI
The goal of the example SSI was to record what MMIO addresses the
`pinctrl-bcm2835.c` driver writes to.

We can run the SSI like so:
```
$ cd pinctrl-example
$ python3 ssi.py
Loaded driver:
    Description: Broadcom BCM2835/2711 pinctrl and GPIO driver
    Author(s): Chris Boot, Simon Arlott, Stephen Warren
    License: GPL
Choose device:
0 : brcm,bcm2835-gpio
1 : brcm,bcm2711-gpio
2 : brcm,bcm7211-gpio
Choice: 
```
Chose device `0`, then you will get a gdb-style prompt:
```
Choice: 0
ssi >
```
Here you have a number of options:
- `probe` will run the probe method for this driver, `bcm2835_pinctrl_probe(...)`
- `enable-irq [which]` will run the corresponding enable-irq method,
  `bcm2835_gpio_irq_enable(...)`
- `b [line number]` will set a breakpoint on the corresponding line number
- `xc [code]` will execute the C code provided in `code` and display the result
- `pm` will pretty-print the entire memory tree
- `verbose [fn_name] [arg1-format] [arg2-format] ...` will intercept any calls
  to `fn_name` and prints its arguments according to the format strings in
  `arg[i]-format`. Use `x` to print in hex.

Combining these, if we want to know which MMIO addresses are written to we can
intercept calls to `writel` using `verbose`:
```
ssi > verbose writel x x
ssi >
```
then run the `probe` method to see what it writes:
```
ssi > probe
Line 253: writel(val, pc->base + reg) => 0, 7e20004c
...
ssi >
```
which answers our question. We could be curious as well how to enable
interrupts for a certain pin, which we can answer similarly:
```
ssi > enable-irq 1
Line 253: writel(val, pc->base + reg) => 2, 7e20004c
ssi > enable-irq 2
Line 253: writel(val, pc->base + reg) => 4, 7e20004c
ssi >
```

We can then set breakpoints, use `xc` (execute-C) to view the values of locals
like `offset`, and then `c`ontinue execution:
```
ssi > b 498
ssi > enable-irq 3
ssi :: On line 498
ssi > xc offset
(1209, 0) = 3
ssi > c
Line 253: writel(val, pc->base + reg) => 8, 7e20004c
ssi >
```

## Using the SSI Framework
There is an example lightweight SSI built off the framework in
`simple-example/ssi.py`. It demonstrates the following features:
- Registering Python handlers for C-level method calls (in this case, to catch
  the `ssi_explain` call).
- Using `trace.Value.explain` to get a trace/explanation for a given value.
- Breakpoints (`b`), memory printing (`pm`), C expression execution (`xc`),
  executing from an arbitrary line (`xl`), marking methods verbose.

Here is an example run:
```
$ cd simple-example
$ cat test_input.c
int main() {
    int x = 5;
    x += 3;
    ssi_explain(x);
    return 0;
}
$ python3 ssi.py test_input.c
ssi > xc main()
Value: 8
|   Explanation: ( x ) + ( 3 ) on line 3
|   Value: 5
|   |   Explanation: 5 on line 2
|   Value: 3
|   |   Explanation: 3 on line 3
(6, 0) = 0
ssi > xl 3
Value: [<function Trace.emit_.<locals>.<lambda> at 0x7fbeb1e47400>, <framework.trace.Value object at 0x7fbeb1e436a0>, <framework.trace.Value object at 0x7fbeb1e43520>]
|   Explanation: ( x ) + ( 3 ) on line 3
|   Value: ['opaque', 23]
|   |   Explanation: ( x ) + ( 3 ) on line 3
|   |   Value: <Memref: (9, 0)>
|   |   |   Explanation: x on line 3
|   Value: 3
|   |   Explanation: 3 on line 3
ssi >
```
Notice that when we start executing from line 3, instead of from the top of
`main`, the initial value of `x` is opaque (symbolic).

## Modifying the SSI Framework
The SSI framework lives in the `framework/` directory. It is broken into five
files:

- `lex.py` handles lexing. It is unlikely you will need to touch this file.
- `peg.py` is a miniature PEG parser library for Python. It is also unlikely
  you will need to touch this file.
- `miniparse.py` contains grammar definitions used by the interpreter(s). The
  grammar is broken up into a control flow grammar and an expression grammar.
  Both are highly modular; new syntactic forms should be able to be inserted
  easily once you've determined the relative precedence. Use the `balanced`
  rules liberally to do parsing-with-holes.
- `trace.py` handles the underlying memory representation. Operations are
  performed on this representation using a small intermediate representation
  (see `Trace.emit_`). `Trace.explain` can be used to record the location in
  the input file that caused this operation (useful for tracing back
  explanations of values).
- `interpreter.py` handles control flow and converts C code to the intermediate
  representation accepted by `Trace.emit`. It has its own intermediate
  representation that it aggressively lowers control flow to, e.g., all
  branching instructions are replaced with a core `goto_ite` instruction.

Other than the parsing-with-holes approach described in the paper, there are a
few particularly non-obvious things about the interpreter that are worth
clarifying here:

First, memory is represented as a tree, not a linear array. Every object in
memory gets its own subtree of memory. This allows us to allocate space for
objects even if we don't know their size ahead of time. This is the same
approach taken by many symbolic execution engines, e.g., see the KLEE paper.
This can in theory cause correctness issues if pointers are meant to alias in
non-obvious ways.

Second, memory values are currently represented as Python-native types (e.g.,
`int`s). We do not yet properly handle overflow or "reinterpret casts" (e.g.,
`int *` to `char *`).

Third, we have a mini DSL for expressing lowering rules. See calls to
`self.lexing.fancy_rewrite` in `interpreter.py`. For example, this call:
```
self.lexing.fancy_rewrite(tree, self.trace,
    "while (...) ...",
    "[lchk]: if ({0}) {{ {1} goto [lchk]; }} [lend]: 0;")
```
essentially anti-unifies the input parse tree against the pattern
`while (...) ...`, then rewrites it to the form
```
[lchk]: if ({0}) {
    {1} goto [lchk];
}
[lend]: 0;
```
where `{0}` and `{1}` are replaced with the contents of the first and second
`...` in the pattern, respectively, and `[lchk]` and `[lend]` are replaced with
fresh labels. This rewriting is done directly on the lexed representation of
the source. The rewriter keeps track of the original text that we have
overwritten to provide more helpful user messages when needed. The antiunifier
re-uses the tree structure of `self.trace` to avoid having to re-parse, e.g.,
balanced parentheses.

## Warning & Status of Implementation
Please be warned that this is a prototype, incomplete, work-in-progress
implementation of an SSI framework. Things that are guaranteed not to work
correctly yet:
- "Reinterpret casts," e.g., casting an `int *` to a `char *` to inspect
  individual bytes of an int. Or, more broadly, anything that relies on values
  having actualy bit widths.
- Branching on opaque/symbolic values currently always takes the positive
  branch.
- Only a small number of numerical operations are supported, but you should be
  able to add more by adding them to the list in
  `framework/trace.py:Trace.emit_`.
- Support for symbolic values is rudimentary; it cannot tell that, e.g.,
  `(x+y)==(y+x)`.

On the positive side of things, the entire framework is self-contained and only
about 1k lines of code, so it shouldn't be _terribly_ difficult to debug things
(will be working on comments soon ...). If you have a small example that fails
I'm happy to take a look.

## License
AGPLv3, see `LICENSE`. The example code demonstrated in `pinctrl-example` is
available under the GPLv2 license.
