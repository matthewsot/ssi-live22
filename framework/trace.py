"""Machine & memory model the interpreter executes against

This file should probably be renamed; it no longer produces a trace, instead it
defines a representation of machine memory, registers, and operations
manipulating such.

The basic organization is as follows:
    - A Trace keeps track of a tree-shaped memory
    - Each node in the memory is a Memref
    - Memrefs can have a Value and/or be further subdivided into children
      Memrefs. Child Memrefs represent, e.g., fields of a struct or entries in
      an array. The address of a Memref is its path down the memory tree.
    - Values represent contents of cells in memory. Values can be concrete or
      opaque. Each value is actually an equivalence class of values, so it can
      start off opaque and then we realize later (e.g., by going down a path
      with if (!v)) that it is equivalent to, say, the constant 0 value. Note
      we don't currently really do much of this as we don't really do much path
      scheduling. But the ability is there.
    - Opaque Values can be coerced into a Memref, e.g., if you dereference a
      new pointer, which will cause a new node in the Memref tree to be
      allocated for it to point to. This is essentially like calling
      malloc(infinity) every time you dealloc a fresh pointer.
"""
import copy
import framework.lex as lex

class Value:
    """Represents an equivalence class of values"""
    def __init__(self, value, concrete, trace):
        """Create a new Value

        To make an opaque value, set @value to ["opaque", id] and @concrete to
        False. Otherwise, set @value to the concrete value (e.g., an int).

        In addition to 'raw' opaque values and concrete values, you can have an
        expression on opaques. This looks like [lambda, val1, val2, ...].
        Later, when a value is needed (e.g., to dereference), we will ask for
        val1, ...'s concrete values and then apply lambda to them. To use this,
        pass the expression list to @value and set @concrete to True.

        Every equivalence class has a canonical element (the "most concrete"
        version). This implementation is pretty shoddy; we essentially assume
        each equivalence class has at most two elements. This should be
        improved before moving to more extensive symbolic execution/path
        scheduling.
        """
        self.trace = trace
        self.value = value
        self.concrete = concrete
        self.explanation = trace.val_explanation
        self.canonical = self
        self.recursive_mem = False
        assert not (isinstance(self.value, list) and self.concrete
                    and self.value[0] == "opaque")

    def cval(self):
        """Returns the value of the canonical element in the equivalence class
        """
        return self.canonical.value

    def explain(self, depth=0):
        """Prints a trace of modifications to this value"""
        if isinstance(self.canonical.value, list):
            print("|   " * depth + "Value: [opaque expr]")
        else:
            print("|   " * depth + "Value:", self.canonical.value)
        depth += 1
        print("|   " * depth + "Explanation:",
                " ".join([l.string for l in self.explanation[0]
                          if isinstance(l, lex.Lexeme)]),
                "on line", self.explanation[0][0].line_number)
        for child in self.explanation[1:]:
            child.explain(depth)

    def opaque_reason(self):
        """Explains why this value is opaque"""
        if isinstance(self.cval(), list) and self.cval()[0] == "opaque":
            return (" ".join([l.string for l in self.explanation[0]
                              if isinstance(l, lex.Lexeme)]) +
                    " on line " + str(self.explanation[0][0].line_number))
        best_reason = None
        for child in self.explanation[1:]:
            best_reason = best_reason or child.opaque_reason()
        return best_reason

    def as_memref(self):
        """Explains why this value is opaque"""
        if self.canonical.concrete:
            return self.cval()
        elif self.cval()[0] == "opaque":
            assert self.canonical == self
            self.canonical = Value(self.trace.memory.append().child(0),
                                   True, self.trace)
            return self.cval()
        concretized = [arg.as_memref() for arg in self.cval()[1:]]
        concrete = self.cval()[0](*concretized)
        assert self.canonical == self
        self.canonical = Value(concrete, True, self.trace)
        return self.canonical.value

    def pprint(self):
        """Pretty prints the value"""
        if self.canonical is not self:
            return self.canonical.pprint()
        if isinstance(self.value, Memref):
            print(self.value.address)
        else:
            print(self.canonical.value)

class Memref:
    """Represents a memory cell or range of memory cells in the memory tree"""
    def __init__(self, parent, address, value, trace=None):
        """Create a new Memref under @parent with the given address and value
        """
        self.trace = trace if trace else parent.trace
        self.parent = parent
        self.address = address
        self.children = []
        self.value = value

    def get_value(self):
        """Returns a Value representing the memory location.

        If this represents a range of memory locations, it gives back a
        "summary value" that's basically a dictionary.
        """
        if not self.children:
            return self.value
        summary = Value([self.value]
                        + [(child.address[-1], child.get_value())
                           for child in self.children],
                        True, self.trace)
        summary.recursive_mem = True
        return summary

    def set_value(self, value):
        """Assign a value to the memory range

        If the value is the result of get_value compressing a range of memory
        locations, it will expand that value. This is useful for, e.g., copying
        structs. NOTE: This may give incorrect C semantics when copying an
        array into a pointer?
        """
        if isinstance(value, Value) and value.recursive_mem:
            self.value = value.value[0]
            # TODO: Need to clear existing children?
            for idx, subvalue in value.value[1:]:
                self.child(idx).set_value(subvalue)
        else:
            self.value = value

    def pyify(self, memo=None):
        """Converts a memory cell to a corresponding Python value.

        Basically, memory ranges become lists.
        """
        memo = memo or dict()
        if self.address not in memo:
            memo[self.address] = []
            if self.children:
                for child in self.children:
                    memo[self.address].append(child.pyify(memo))
            else:
                value = self.value.cval()
                if isinstance(value, Memref):
                    memo[self.address].append(value.pyify(memo))
                else:
                    memo[self.address] = value
        return memo[self.address]

    def print_pyify(self, memo=None, depth=0):
        """Pretty-prints the memory range"""
        memo = memo or set()
        if self.address in memo:
            print("|   " * depth + str(self.address))
        else:
            memo.add(self.address)
            if self.children:
                print("|   " * depth + str(self.address))
                for child in self.children:
                    child.print_pyify(memo, depth + 1)
            else:
                value = self.value.cval()
                print("|   " * depth + str(self.address) + " = " + str(value))
                if isinstance(value, Memref):
                    value.print_pyify(memo, depth + 1)

    def field(self, name):
        """Given a Memref representing a struct, gets a Memref for a field

        Currently, we're just associating each field label with an index and
        treating it as an array lookup.
        """
        if name not in self.trace.offsets:
            self.trace.offsets[name] = Value(len(self.trace.offsets),
                                             True, self)
        return self.child(self.trace.offsets[name].cval())

    def append(self):
        """Append a new child"""
        if self.children:
            return self.children[-1] + 1
        return self.child(self.address + (0,))

    def child(self, address):
        """Gets a particular child of the node, or insert if not existing

        This is slightly non-trivial because we store children in a sparse
        list. Maybe we should use a defaultdict.
        """
        if isinstance(address, int): address = self.address + (address,)
        assert address[:-1] == self.address
        i = -1
        for i, child in enumerate(self.children):
            if child.address == address:
                return child
            if child.address > address:
                i = i - 1
                break
        i += 1
        self.children.insert(i, Memref(self, address, self.trace.opaque()))
        return self.children[i]

    def lookup(self, address):
        """Recursive child lookup"""
        assert address[:len(self.address)] == self.address
        if address == self.address:
            return self
        child = self.child(address[:len(self.address) + 1])
        return child.lookup(address)

    def pprint(self, depth=0):
        """Pretty-print the memory location"""
        prefix = "-" * depth
        print(prefix + f"{self.address} = ", end='')
        if isinstance(self.value, Value): self.value.pprint()
        else: print(self.value)
        for child in self.children:
            child.pprint(depth + 1)

    def __add__(self, rhs):
        """Pointer addition"""
        next_address = self.address[:-1] + (self.address[-1] + rhs,)
        return self.parent.child(next_address)

    def __str__(self):
        """Compact string representation"""
        return "<Memref: " + str(self.address) + ">"

class Trace:
    """Manages state for an interpretation run"""
    def __init__(self):
        """Initialize a new Trace"""
        self.counter = 0
        self.explanation_stack = [None]
        self.pause_explanation_ = 0

        # Maps string -> dstloc
        self.scopes = [dict()]

        self.offsets = dict()
        self.memory = Memref(None, tuple(), None, trace=self)
        self.val_explanation_stack = []

    def emit(self, expr):
        """Perform an operation on the state

        This is a lightweight wrapper around Trace.emit_ that saves
        explanations for the values so that users can trace back where a value
        came from.
        """
        self.val_explanation_stack.append([self.explanation])
        result = self.emit_(expr)
        self.val_explanation_stack.pop()
        if self.val_explanation_stack and isinstance(result, Value):
            self.val_explanation_stack[-1].append(result)
        return result

    def emit_(self, expr):
        """Perform an operation on the state

        This is a little mini DSL, desugured from the DSL in Interpreter.emit.
        Instructions are S-expressions of the form [op, arg1, ...]. Arguments
        are usually Values or other S-expressions.
        """
        if isinstance(expr, Value): return expr
        op = expr[0]
        if op in ("+", "==", "!=", "<") or op.startswith("bin_"):
            if op.startswith("bin_"): op = op[len("bin_"):]
            if op == "/": op = "//"
            _, src1, src2 = expr
            return self.operate(lambda x, y: eval(f"x {op} y"),
                                [self.emit(src1), self.emit(src2)])
        elif op in ("-", "~"):
            _, src1 = expr
            return self.operate(lambda x: eval(f"{op}{x}"), [self.emit(src1)])
        elif op == "assert":
            _, claim = expr
            # TODO: do something with this. Assertions are not assertions at
            # the source level, rather they're symbolic assertions. If we go
            # down a branch if (x == 0) and x is opaque, the interpreter should
            # emit, essentially, (assert (== x (imm 0))).
        elif op == "imm":   # Immediate
            _, imm = expr
            return Value(imm, True, self)
        elif op == "*":     # Dereference
            _, src = expr
            return self.emit(src).as_memref().get_value()
        elif op == "str":   # Store into a new memory slot
            _, src = expr
            new_memref = self.memory.append().child(0)
            new_memref.set_value(self.emit(src))
            return Value(new_memref, True, self)
        elif op == "upd":   # Update a location in memory
            _, src, dst = expr
            self.emit(dst).as_memref().set_value(self.emit(src))
        elif op == "opaque": # Create a new opaque value
            return self.opaque()
        elif op == "field":
            # Called like (field struct-ptr (imm "field name"))
            # Field names are implicitly converted to numerical offsets
            _, headptr, src = expr
            field_name = self.emit(src).value
            assert isinstance(field_name, str) # NOT passed by reference
            if field_name not in self.offsets:
                self.offsets[field_name] = Value(len(self.offsets), True, self)
            head = self.emit(headptr).as_memref().child(0)
            return Value(head + self.offsets[field_name].canonical.value,
                         True, self)
        else:
            print(expr)
            raise NotImplementedError

    @property
    def val_explanation(self):
        """Gets a source-level explanation for the current operation"""
        if not self.val_explanation_stack:
            return [self.explanation]
        return self.val_explanation_stack[-1]

    # TODO value explanations
    def operate(self, operator, vals):
        """Try to perform an operation on a set of values

        If the values are all conccrete, it will simply perform the operation
        and return a corresponding new Value. Otherwise, it will make a
        symbolic Value with that expression tree.
        """
        if not all(val.canonical.concrete for val in vals):
            return Value([operator] + vals, False, self)
        return Value(operator(*[v.canonical.value for v in vals]), True, self)

    def push_scope(self, param_names, args):
        """Push a new scope on to the scope stack (e.g., calling function)"""
        self.scopes.append(dict(zip(param_names, args)))

    def pop_scope(self):
        """Pop a scope from the stack"""
        self.scopes.pop()

    def local(self, name):
        """Return a local variable from the latest scope

        If no such variable with this name exists, will create it in the
        bottom-most scope.
        """
        for scope in self.scopes:
            if name in scope:
                return scope[name]
        self.scopes[-1][name] = self.opaque()
        return self.scopes[-1][name]

    def explain(self, explanation):
        """Syntax sugar for pushing/popping from the explanation stack

        Basically, when the interpreter starts operating on a region of the
        input, it calls trace.explain(lexemes) to indicate that any operations
        performed are a result of execution of those lexemes.
        """
        class __ExplainContext:
            def __enter__(_):
                if not self.pause_explanation_:
                    self.explanation_stack.append(explanation)
            def __exit__(_, __, ___, ____):
                if not self.pause_explanation_:
                    self.explanation_stack.pop()
        return __ExplainContext()

    def freeze_explanation(self):
        """While this context is entered, no new explanations are pushed/popped

        Useful when executing code directly generated by a stub.
        """
        class __PauseExplainContext:
            def __enter__(_): self.pause_explanation_ += 1
            def __exit__(_, __, ___, ____): self.pause_explanation_ -= 1
        return __PauseExplainContext()

    @property
    def explanation(self):
        """Most-precise explanation of the current operation"""
        return self.explanation_stack[-1]

    def uid(self):
        """Global counter"""
        self.counter += 1
        return self.counter - 1

    def gen_labels(self, num):
        """Generate probably-unique C identifiers"""
        return [f"___l{self.uid()}" for _ in range(num)]

    def opaque(self):
        """New unique opaque Value"""
        return Value(["opaque", self.uid()], False, self)
