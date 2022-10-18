import copy
import framework.lex as lex

class Value:
    def __init__(self, value, concrete, trace):
        self.trace = trace
        self.value = value
        self.concrete = concrete
        self.explanation = trace.val_explanation
        self.canonical = self
        self.recursive_mem = False
        if isinstance(self.value, list) and self.concrete and self.value[0] == "opaque":
            raise NotImplementedError

    def cval(self):
        return self.canonical.value

    def explain(self, depth=0):
        print("|   " * depth + "Value:", self.canonical.value)
        depth += 1
        print("|   " * depth + "Explanation:",
                " ".join([l.string for l in self.explanation[0]
                          if isinstance(l, lex.Lexeme)]),
                "on line", self.explanation[0][0].line_number)
        for child in self.explanation[1:]:
            child.explain(depth)

    def opaque_reason(self):
        if isinstance(self.cval(), list) and self.cval()[0] == "opaque":
            return (" ".join([l.string for l in self.explanation[0]
                              if isinstance(l, lex.Lexeme)]) +
                    " on line " + str(self.explanation[0][0].line_number))
        best_reason = None
        for child in self.explanation[1:]:
            best_reason = best_reason or child.opaque_reason()
        return best_reason

    def as_memref(self):
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
        if self.canonical is not self: return self.canonical.pprint()
        if isinstance(self.value, Memref): print(self.value.address)
        else: print(self.canonical.value)

class Memref:
    def __init__(self, parent, address, value, trace=None):
        self.trace = trace if trace else parent.trace
        self.parent = parent
        self.address = address
        self.children = []
        self.value = value

    def get_value(self):
        if not self.children:
            return self.value
        summary = Value([self.value] + [(child.address[-1], child.get_value()) for child in self.children], True, self.trace)
        summary.recursive_mem = True
        return summary

    def pyify(self, memo=None):
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

    def __str__(self):
        return "<Memref: " + str(self.address) + ">"

    def set_value(self, value):
        if isinstance(value, Value) and value.recursive_mem:
            self.value = value.value[0]
            for idx, subvalue in value.value[1:]:
                self.child(idx).set_value(subvalue)
        else:
            self.value = value

    def field(self, name):
        if name not in self.trace.offsets:
            self.trace.offsets[name] = Value(len(self.trace.offsets), True, self)
        return self.child(self.trace.offsets[name].cval())

    def __add__(self, rhs):
        next_address = self.address[:-1] + (self.address[-1] + rhs,)
        return self.parent.child(next_address)

    def append(self):
        if self.children:
            return self.children[-1] + 1
        return self.child(self.address + (0,))

    def child(self, address):
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
        assert address[:len(self.address)] == self.address
        if address == self.address:
            return self
        child = self.child(address[:len(self.address) + 1])
        return child.lookup(address)

    def pprint(self, depth=0):
        prefix = "-" * depth
        print(prefix + f"{self.address} = ", end='')
        if isinstance(self.value, Value): self.value.pprint()
        else: print(self.value)
        for child in self.children:
            child.pprint(depth + 1)

class Trace:
    """A single execution trace.
    """
    def __init__(self):
        self.counter = 0
        self.explanation_stack = [None]
        self.pause_explanation_ = 0

        # Maps string -> dstloc
        self.scopes = [dict()]

        self.offsets = dict()
        self.memory = Memref(None, tuple(), None, trace=self)
        self.val_explanation_stack = []

    # TODO: Value explanations
    def emit(self, expr):
        self.val_explanation_stack.append([self.explanation])
        result = self.emit_(expr)
        self.val_explanation_stack.pop()
        if self.val_explanation_stack and isinstance(result, Value):
            self.val_explanation_stack[-1].append(result)
        return result

    def emit_(self, expr):
        if isinstance(expr, Value): return expr
        op = expr[0]
        if op in ("+", "==", "!=", "<") or op.startswith("bin_"):
            if op.startswith("bin_"): op = op[len("bin_"):]
            if op == "/": op = "//"
            _, src1, src2 = expr
            return self.operate(lambda x, y: eval(f"x {op} y"),
                                [self.emit(src1), self.emit(src2)])
        elif op in "-":
            _, src1 = expr
            return self.operate(lambda x: eval(f"-x"), [self.emit(src1)])
        elif op == "assert":
            _, claim = expr
            # print("ASSERTED:", self.reg(claim).canonical.value)
            # TODO: actually assert it
        elif op == "imm":
            _, imm = expr
            return Value(imm, True, self)
        elif op == "*":
            _, src = expr
            return self.emit(src).as_memref().get_value()
        elif op == "str":
            _, src = expr
            new_memref = self.memory.append().child(0)
            new_memref.set_value(self.emit(src))
            return Value(new_memref, True, self)
        elif op == "upd":
            _, src, dst = expr
            self.emit(dst).as_memref().set_value(self.emit(src))
        elif op == "opaque":
            return self.opaque()
        elif op == "field":
            _, headptr, src = expr
            field_name = self.emit(src).value
            assert isinstance(field_name, str) # NOT passed by reference
            if field_name not in self.offsets:
                self.offsets[field_name] = Value(len(self.offsets), True, self)
            head = self.emit(headptr).as_memref().child(0)
            return Value(head + self.offsets[field_name].canonical.value, True, self)
        else:
            print(instr.expr)
            raise NotImplementedError

    @property
    def val_explanation(self):
        if not self.val_explanation_stack:
            return [self.explanation]
        return self.val_explanation_stack[-1]

    # TODO value explanations
    def operate(self, operator, vals):
        if not all(val.canonical.concrete for val in vals):
            return Value([operator] + vals, False, self)
        return Value(operator(*[v.canonical.value for v in vals]), True, self)

    def push_scope(self, param_names, args):
        self.scopes.append(dict(zip(param_names, args)))

    def pop_scope(self):
        self.scopes.pop()

    def local(self, name):
        for scope in self.scopes:
            if name in scope:
                return scope[name]
        self.scopes[-1][name] = self.opaque()
        return self.scopes[-1][name]

    def explain(self, explanation):
        class __ExplainContext:
            def __enter__(_):
                if not self.pause_explanation_:
                    self.explanation_stack.append(explanation)
            def __exit__(_, _2, _3, _4):
                if not self.pause_explanation_:
                    self.explanation_stack.pop()
        return __ExplainContext()

    def freeze_explanation(self):
        class __PauseExplainContext:
            def __enter__(_): self.pause_explanation_ += 1
            def __exit__(_, _2, _3, _4): self.pause_explanation_ -= 1
        return __PauseExplainContext()

    @property
    def explanation(self):
        return self.explanation_stack[-1]

    def uid(self):
        self.counter += 1
        return self.counter - 1

    def gen_labels(self, num):
        return [f"___l{self.uid()}" for _ in range(num)]

    def opaque(self):
        return Value(["opaque", self.uid()], False, self)
