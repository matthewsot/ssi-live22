"""Basic SSI for the pinctrl driver
"""
from framework.interpreter import *

# Initialize the interpreter
interpreter = Interpreter("pinctrl-bcm2835.c")
true = interpreter.trace.local("true")
true.as_memref().set_value(Value(1, True, interpreter.trace))

# The next step is to "run" all of the global code, like static global
# variables, the MODULE_* lines at the end of the code, etc.
# We'll add some helper methods here overriding the MODULE_* macros to catch
# metadata about the driver an dprint it back to the user.
module_data = dict({"authors": [], "description": None, "license": None, "driver_struct": None})
def register_author(name_reg):
    module_data["authors"].append(name_reg.get_value())
interpreter.register_fn("MODULE_AUTHOR", register_author)
def register_description(descr_reg):
    module_data["description"] = descr_reg.get_value()
interpreter.register_fn("MODULE_DESCRIPTION", register_description)
def register_license(license_reg):
    module_data["license"] = license_reg.get_value()
interpreter.register_fn("MODULE_LICENSE", register_license)
def register_driver_struct(struct_reg):
    module_data["driver_struct"] = struct_reg.cval()
interpreter.register_fn("module_platform_driver", register_driver_struct)

# Run the global code
interpreter.globals_pass()

# Print metadata about the driver
print("Loaded driver:")
print("\tDescription:", module_data["description"])
print("\tAuthor(s):", ", ".join(module_data["authors"]))
print("\tLicense:", module_data["license"])

# This one driver seems to match against a variety of different devices. We can
# find the list of supported device_ids on driver_struct.driver.of_match_table,
# where driver_struct is the struct passed to the module_platform_driver
# earlier during the "global execution" phase:
of_match_table = interpreter.exec_c("{0}.driver.of_match_table[0]",
                                    module_data["driver_struct"]).cval().parent

# Let's give the user a choice between the three different drivers in the
# of_match_table. Each entry is a pair of "compatible" (the device ID) and
# "data" (the corresponding device metadata to use).
print("Choose device:")
datas = []
for i in range(len(of_match_table.children) - 1):
    compatible = interpreter.exec_c(f"{{0}}.driver.of_match_table[{i}].compatible",
                                    module_data["driver_struct"]).get_value()
    print(i, ":", compatible)
    data = interpreter.exec_c(f"{{0}}.driver.of_match_table[{i}].data",
                              module_data["driver_struct"]).get_value()
    datas.append((compatible, data))
data = datas[int(input("Choice: "))]
# data is now a tuple of (device_id_string, bcm_plat_data) to use for this chip

######### Now we model the module-system interface. ########

# Assume we're "compatible" with a device string iff this is the device string
# we chose.
def of_device_is_compatible(np, string):
    string = string.get_value()
    return interpreter.emit("(str (imm {0}))", string == data[0])
interpreter.register_fn("of_device_is_compatible", of_device_is_compatible)

# Helper method to find a device MMIO address in a DTSI file.
def dtsi_find(dtsi_file, device_string):
    from framework.lex import path_to_string
    dtsi = path_to_string(dtsi_file)
    try:
        idx = dtsi.index(f'compatible = "{device_string}";')
    except ValueError:
        print(f"SSI: Could not find {device_string} in the DTSI file {dtsi_file}!")
        print("SSI: Defaulting to address zero...")
        return "0"
    return dtsi[idx:].split(";")[1].split("<")[1].split(" ")[0]

# Requesting a resource should look up the corresponding MMIO address in the
# DTSI file, then assign the out parameter ptr_to_iomem to that address. NOTE:
# This driver never treats it as a pointer, only ever calling writel/readl, so
# we can just return it as an int.
def of_address_to_resource(np, which_resource, ptr_to_iomem):
    assert which_resource.get_value() == 0
    addr = dtsi_find("bcm283x.dtsi", data[0])
    interpreter.exec_c(f"*{{0}} = {addr}", ptr_to_iomem.cval())
    return interpreter.emit("(str (imm {0}))", 0)
interpreter.register_fn("of_address_to_resource", of_address_to_resource)

# I think ioremap_resource is supposed to map the physical address into a
# virtual address for use. We don't really care for this module, so we'll just
# use the physical address directly.
def devm_ioremap_resource(dev, ptr_to_iomem):
    return interpreter.exec_c(f"*{{0}}", ptr_to_iomem.cval())
interpreter.register_fn("devm_ioremap_resource", devm_ioremap_resource)

# I think this is a macro defined somewhere else, this seems to be a reasonable
# interpretation of what it does...
def BIT(val):
    return interpreter.exec_c("(1 << ({0}))", val)
interpreter.register_fn("BIT", BIT)

# Stateful driver information is shared using a struct bcm2835_pinctrl
# structure that the kernel records as the driver data. It is originall
# allocated in the probe, then latter methods can access it by calling
# gpiochip_get_data. We model this by storing a global copy of pc at the SSI
# level, then whenever the user runs "probe" in the REPL we build a fresh pc
# and use it everywhere until "probe" is run again (resetting the state).
pc = None
def gpiochip_get_data(*args):
    return pc
interpreter.register_fn("gpiochip_get_data", gpiochip_get_data)

# The current interpreter does no serious path exploration; if it branches on
# an opaque value, it always takes the positive branch. This is sort of
# annoying when there are lots of if (foo_return_error(...)) return 0;
# error-handling paths, so we stub a bunch of methods as doing nothing other
# than return 0. Long-term, path exploration should remove the need to do this.
def always_zero(*args):
    return interpreter.emit("(str (imm {0}))", 0)
interpreter.register_fn("IS_ERR", always_zero)
interpreter.register_fn("gpiochip_add_data", always_zero)
interpreter.register_fn("readl", always_zero)

# Dereferencing an opaque value forces the interpreter to create a new memory
# range, so allocations can just return new opaque pointers.
def kzalloc(*args):
    return interpreter.emit("(str (str (opaque)))")
interpreter.register_fn("devm_kzalloc", kzalloc)
# the model just has to be "good enough;" for the questions we care about,
# calloc == malloc seems good enough for this code for now.
interpreter.register_fn("devm_kcalloc", kzalloc)
# This basically searches over a set of pointers, we don't care what it finds
# as long as it finds "something," so we can approximate it with a malloc.
interpreter.register_fn("of_match_node", kzalloc)

# Now we do the actual REPL. All the commands other than "probe" and
# "enable-irq" are basically boilerplate that can be copied over to other SSIs.
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
        elif command.startswith("verbose "):
            fn_name = command.split(" ")[1]
            formatters = command.split(" ")[2:]
            interpreter.verbose_fns[fn_name] = formatters
        elif command == "c":
            break
        elif command == "probe":
            probe = module_data["driver_struct"].field("probe").get_value().cval()
            pc = interpreter.trace.opaque()
            interpreter.trace.push_scope(["pc"], [pc])
            interpreter.returnify_fn(probe[1][0])
            interpreter.curr_lexeme = probe[1][0]
            while True:
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
            _, (start_lex, param_names) = fn.get_value()
            interpreter.returnify_fn(start_lex)
            assert param_names == ["data"]
            interpreter.trace.push_scope(param_names, [interpreter.trace.opaque()])
            interpreter.curr_lexeme = start_lex
            while True:
                try: result = interpreter.step()
                except StopIteration: break
                if result and result[0] == "return": break
            interpreter.trace.pop_scope()
        else:
            print(f"ssi > Unknown command '{command}'")
REPL(interpreter)
