import sys
from framework.interpreter import *

if len(sys.argv) != 2:
    print(f"Usage: python3 {sys.argv[0]} [input-file]")
    sys.exit(1)

interpreter = Interpreter(sys.argv[1])
true = interpreter.trace.local("true")
true.as_memref().set_value(Value(1, True, interpreter.trace))

interpreter.verbose_fns["ssi_print"] = ["" for _ in range(50)]
def explain(value):
    # Explain *value
    interpreter.emit("(* {0})", value).explain()
interpreter.register_fn("ssi_explain", explain)

# Pick up function declarations, globals, etc.
for _ in range(1000):
    try:                    interpreter.step()
    except StopIteration:   break

# Then drop in to a REPL
def REPL(interpreter):
    if interpreter.curr_lexeme:
        print("ssi :: On line", interpreter.curr_lexeme.line_number)
    while True:
        command = input("ssi > ")
        if command == "pm":
            interpreter.trace.memory.print_pyify()
        elif command.startswith("b "):
            line_number = int(command.split(" ")[1])
            interpreter.break_lines[line_number] = REPL
        elif command.startswith("xc "):
            command = command[len("xc "):]
            result = interpreter.exec_c(command)
            result.as_memref().print_pyify()
        elif command.startswith("xl "):
            line_number = int(command[len("xl "):])
            interpreter.set_to_line(line_number)
            for _ in range(1000):
                try:                    interpreter.step()
                except StopIteration:   break
        elif command.startswith("verbose "):
            fn_name = command.split(" ")[1]
            formatters = command.split(" ")[2:]
            interpreter.verbose_fns[fn_name] = formatters
        elif command == "c":
            break
        else:
            print(f"ssi > Unknown command '{command}'")
REPL(interpreter)
