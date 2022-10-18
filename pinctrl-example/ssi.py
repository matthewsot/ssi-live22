from framework.interpreter import *

interpreter = Interpreter("pinctrl-bcm2835.c")
true = interpreter.trace.local("true")
true.as_memref().set_value(Value(1, True, interpreter.trace))

# Execute any MODULE_* lines, saving data about the module.
module_data = dict({"authors": [], "description": None, "license": None, "driver_struct": None})
def register_author(name_reg):
    module_data["authors"].append(name_reg.value.value.value)
interpreter.register_fn("MODULE_AUTHOR", register_author)
def register_description(descr_reg):
    module_data["description"] = descr_reg.value.value.value
interpreter.register_fn("MODULE_DESCRIPTION", register_description)
def register_license(license_reg):
    module_data["license"] = license_reg.value.value.cval()
interpreter.register_fn("MODULE_LICENSE", register_license)
def register_driver_struct(struct_reg):
    module_data["driver_struct"] = struct_reg.cval()
interpreter.register_fn("module_platform_driver", register_driver_struct)

for _ in range(1000):
    try:                    interpreter.step()
    except StopIteration:   break

# OR, we just run everything starting from the zero and tell it to skip things
# it doesn't understand.
# interpreter.run_matching("(seq (/ (str MODULE_AUTHOR) (str MODULE_DESCRIPTION) (str MODULE_LICENSE) (str module_platform_driver)) (skipto (str ;)))")

print("Loaded driver:")
print("\tDescription:", module_data["description"])
print("\tAuthor(s):", ", ".join(module_data["authors"]))
print("\tLicense:", module_data["license"])
# print("\tDriver Struct:", module_data["driver_struct"])
probe = module_data["driver_struct"].field("probe").get_value().cval()
# print("\tProbe:", probe)

of_match_table = interpreter.exec_c("{0}.driver.of_match_table[0]",
                                    module_data["driver_struct"]).cval().parent

print("Choose device:")
datas = []
for i in range(len(of_match_table.children) - 1):
    compatible = interpreter.exec_c(f"{{0}}.driver.of_match_table[{i}].compatible",
                                    module_data["driver_struct"]).cval().get_value().cval()
    print(i, ":", compatible)
    data = interpreter.exec_c(f"{{0}}.driver.of_match_table[{i}].data",
                              module_data["driver_struct"]).cval().get_value().cval()
    datas.append((compatible, data))
data = datas[int(input("Choice: "))]

def kzalloc(*args):
    return interpreter.emit("(str (str (opaque)))")
interpreter.register_fn("devm_kzalloc", kzalloc)
interpreter.register_fn("devm_kcalloc", kzalloc)

def of_device_is_compatible(np, string):
    string = string.cval().get_value().cval()
    return interpreter.emit("(str (imm {0}))", string == data[0])
interpreter.register_fn("of_device_is_compatible", of_device_is_compatible)

def of_address_to_resource(np, which_resource, ptr_to_iomem):
    assert which_resource.cval().get_value().cval() == 0
    from framework.lex import path_to_string
    dtsi = path_to_string("bcm283x.dtsi")
    idx = dtsi.index(f'compatible = "{data[0]}";')
    reg = dtsi[idx:].split(";")[1].split("<")[1].split(" ")[0]
    interpreter.exec_c(f"*{{0}} = {reg}", ptr_to_iomem.cval())
    return interpreter.emit("(str (imm {0}))", 0)
interpreter.register_fn("of_address_to_resource", of_address_to_resource)

def is_err(*args):
    return interpreter.emit("(str (imm {0}))", 0)
interpreter.register_fn("IS_ERR", is_err)
interpreter.register_fn("gpiochip_add_data", is_err)

def devm_ioremap_resource(dev, ptr_to_iomem):
    return interpreter.exec_c(f"*{{0}}", ptr_to_iomem.cval())
interpreter.register_fn("devm_ioremap_resource", devm_ioremap_resource)

def of_match_node(*args):
    return interpreter.emit("(str (str (opaque)))")
interpreter.register_fn("of_match_node", of_match_node)

pc = None
def gpiochip_get_data(*args):
    return pc
interpreter.register_fn("gpiochip_get_data", gpiochip_get_data)

def readl(*args):
    return interpreter.emit("(str (imm {0}))", 0)
interpreter.register_fn("readl", readl)

def BIT(val):
    return interpreter.exec_c("(1 << ({0}))", val)
interpreter.register_fn("BIT", BIT)

interpreter.register_fn("explain", lambda x: interpreter.emit("(* {0})", x).explain())

def REPL(interpreter):
    global pc
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
        elif command == "probe":
            pc = interpreter.trace.opaque()
            interpreter.trace.push_scope(["pc"], [pc])
            interpreter.returnify_fn(probe[1][0])
            interpreter.curr_lexeme = probe[1][0]
            for _ in range(200):
                try: result = interpreter.step()
                except StopIteration: break
                if result and result[0] == "return": break
            interpreter.trace.pop_scope()
        elif command.startswith("enable-irq "):
            which_one = int(command.split(" ")[1])
            def irqd_to_hwirq(*args):
                return interpreter.exec_c(f"{which_one}")
            interpreter.register_fn("irqd_to_hwirq", irqd_to_hwirq)
            fn = interpreter.exec_c("{0}->gpio_chip.irq.chip->irq_enable", pc.cval())
            _, (start_lex, param_names) = fn.cval().get_value().cval()
            interpreter.returnify_fn(start_lex)
            assert param_names == ["data"]
            interpreter.trace.push_scope(param_names, [interpreter.trace.opaque()])
            interpreter.curr_lexeme = start_lex
            for _ in range(200):
                try: result = interpreter.step()
                except StopIteration: break
                if result and result[0] == "return": break
            interpreter.trace.pop_scope()
        elif command.startswith("verbose "):
            fn_name = command.split(" ")[1]
            formatters = command.split(" ")[2:]
            interpreter.verbose_fns[fn_name] = formatters
        elif command == "c":
            break
        else:
            print(f"ssi > Unknown command '{command}'")
REPL(interpreter)
