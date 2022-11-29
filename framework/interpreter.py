"""Interpreter for C code

Essentially, takes paths through parse trees produced by miniparse.py, desugars
them with fancy_rewrite in lex.py, and then executes them via a mini IR
understood by trace.py.
"""
import framework.lex as lex
from framework.miniparse import *
import sys
from framework.trace import *
from framework.peg import parse_expr_str

class Interpreter:
    """Main interpreter class"""
    def __init__(self, file_name):
        """Initialize a new interpreter from a source file"""
        self.file_name = file_name
        self.lexing = lex.lex_c(lex.path_to_string(file_name))
        self.trace = Trace()
        self.fn_handlers = dict({None: self.default_fn_handler})
        self.verbose_fns = dict()
        self.curr_lexeme = self.lexing.lexemes[0]
        self.break_lines = dict()
        self.is_globals_pass = False

    def globals_pass(self):
        """Runs a pass over globals in the file"""
        self.curr_lexeme = self.lexing.lexemes[0]
        self.is_globals_pass = True
        while True:
            try:                    self.step()
            except StopIteration:   break
        self.is_globals_pass = False

    def set_to_line(self, line):
        """Set the execution head to a given line number"""
        self.curr_lexeme = self.lexing.after_line_number(line)[0]

    def register_fn(self, name, handler):
        """Register a Python handler for a source-level function"""
        self.fn_handlers[name] = handler

    def step(self):
        """Interpret for a single step from self.curr_lexeme

        If a return ... statement is reached, will return ["return",
        return_value].
        """
        if self.curr_lexeme is None: raise StopIteration
        if self.curr_lexeme.line_number in self.break_lines:
            self.break_lines[self.curr_lexeme.line_number](self)
        # print("EXECUTING LINE:", self.curr_lexeme.line_number)
        assert self.curr_lexeme.lexing is self.lexing
        start_i = self.lexing.lexemes.index(self.curr_lexeme)
        lexemes = self.lexing.lexemes[start_i:]
        # print(" ".join([l.string for l in lexemes[:100]]))
        tree, lexemes = parse_some_cf(lexemes)
        return self.interpret(tree)

    def exec_c(self, string, *args):
        """Executes C code directly

        TODO: Should this always be wrapped in a freeze_explanations()?
        """
        arg_names = self.trace.gen_labels(len(args))
        for i, label in enumerate(arg_names):
            string = string.replace(f"{{{i}}}", f" {label} ")
        string = string.replace("{", "{{").replace("}", "}}")

        old_lexing, old_lexeme = self.lexing, self.curr_lexeme
        new_lexemes = self.lexing.prepend(self.lexing.lexemes[0],
                f"void ___ssi_code() {{{{ return {string}; }}}}")
        self.curr_lexeme = next(l for l in new_lexemes if l.string == "return")

        self.trace.push_scope(arg_names,
                              [a if isinstance(a, Value)
                               else Value(a, True, self.trace)
                               for a in args])
        while True:
            try:
                result = self.step()
                if result and result[0] == "return":
                    break
            except StopIteration: break
        self.lexing, self.curr_lexeme = old_lexing, old_lexeme
        self.trace.pop_scope()
        return result[1]

    def replace_stmts(self, lexemes, replace_stmt, skip_over,
                      replace_with_str):
        """Look for a type of statement and rewrite matches

        Mostly used to rewrite break; statements inside of loops to the correct
        gotos.
        """
        replace_me = find_stmts(lexemes, (replace_stmt,), skip_over)
        for tree in replace_me:
            self.lexing.rewrite([relex(tree)[0]], replace_with_str)

    def interpret(self, tree):
        """Main interpreter method

        Goal is to interpret a single statement, then set curr_lexeme to
        wherever we should keep interpreting from next. This will be called
        (via Interpreter.step) in a loop by the SSI.

        Return value can be None (default) or ["retrun", return_value] if it's
        a return statement.
        """
        if tree[0] in ("Function",):
            bal_id = next(i for i, s in enumerate(tree) if s[0] == "bal")
            fn_name_lex = relex(tree[bal_id - 1])[-1]
            memloc = self.trace.local(fn_name_lex.string)
            self.emit("(upd (imm {0}) {1})", ("fn", find_fn(fn_name_lex)), memloc)
            self.curr_lexeme = relex(tree)[-1].next_lexeme()
        elif tree[0] in ("Preproc",):
            macro = parse_macro(relex(tree)[0].string)
            if macro is None:
                self.curr_lexeme = self.curr_lexeme.next_lexeme()
                return
            if macro["args"] is None:
                to_replace = [l for l in relex(tree)[-1].suffix() if l.string == macro["name"]]
                pattern = " ".join(macro["pattern"]).replace("{", "{{").replace("}", "}}")
                for lexeme in to_replace:
                    self.lexing.rewrite([lexeme, lexeme], pattern)
            else:
                starts = [l for l in relex(tree)[-1].suffix()
                          if l.string == macro["name"] and l.next_lexeme().string == "("]
                for lexeme in starts:
                    all_lexemes, args = [lexeme, lexeme.next_lexeme()], [[]]
                    l = all_lexemes[-1]
                    depth = 0
                    while True:
                        l = l.next_lexeme()
                        all_lexemes.append(l)
                        if l.string == "(": depth += 1
                        if l.string == ")": depth -= 1
                        if depth < 0: break
                        if l.string == "," and depth == 0: args.append([])
                        else:               args[-1].append(l)
                    pattern = ""
                    for x in macro["pattern"]:
                        if isinstance(x, str):
                            pattern += " " + x.replace("{", "{{").replace("}", "}}")
                        elif isinstance(x, int):
                            pattern += " " + f"{{{x}}}"
                        elif x[0] == "strify":
                            assert x[0] == "strify"
                            pattern += " " + "\"" + " ".join([l.string for l in args[x[1]]]) + "\""
                        elif x[0] == "pasteify":
                            pattern += " ".join([l.string for l in args[x[1]]])
                        elif x[0] == "pasteify-str":
                            pattern += x[1]
                        else:
                            raise NotImplementedError
                    self.lexing.rewrite(all_lexemes, pattern, dict({str(i): v for i, v in enumerate(args)}))
            self.curr_lexeme = relex(tree)[-1].next_lexeme()
            self.lexing.rewrite(relex(tree), "")
            return
        elif tree[0] == "Statement":
            return self.interpret(tree[1])
        elif tree[0] == "Label":
            self.curr_lexeme = relex(tree[3])[0]
        elif tree[0] == "Return":
            return ["return", self.interpret_expr(parse_some_expr(relex(tree[2][1]))) if relex(tree[2][1]) else None]
        elif tree[0] == "GotoITE":
            cond = self.interpret_expr(parse_some_expr(relex(tree[2])))
            if_label, else_label = tree[3], tree[4]
            with self.trace.explain(relex(tree)):
                cond_val = self.trace.emit(("*", cond)).cval()
                # TODO: branch scheduling
                if cond_val:
                    self.emit("(assert (!= (* {0}) (imm {1})))", cond, 0)
                    # TODO: Won't work if the same label exists in other functions...
                    jump_to = [l for l in self.lexing.lexemes
                               if l.string == if_label.string and l.next_lexeme().string == ":"]
                    self.curr_lexeme = jump_to[0]
                else:
                    # TODO: Always taking the if branch...
                    self.emit("(assert (== (* {0}) (imm {1})))", cond, 0)
                    # TODO: Won't work if the same label exists in other functions...
                    jump_to = [l for l in self.lexing.lexemes
                               if l.string == else_label.string and l.next_lexeme().string == ":"]
                    self.curr_lexeme = jump_to[0]
        elif tree[0] == "Goto":
            _, new = self.lexing.fancy_rewrite(tree, self.trace,
                    "goto ...;", "goto_ite (1) {0} {0};")
            self.curr_lexeme = new[0]
        elif tree[0] == "For":
            labels, new = self.lexing.fancy_rewrite(tree, self.trace,
                    "for (...; ...; ...) ...",
                    """{0}; goto [lchk]; [lupd]: {2};
                       [lchk]: goto_ite ({1}) [lloop] [lend];
                       [lloop]: {3} goto [lupd];
                       [lend]: 0;""")
            self.replace_stmts(relex(tree)[1:], "Break", ("For", "While", "DoWhile", "Switch"),
                               f"goto {labels['lend']}")
            self.replace_stmts(relex(tree)[1:], "Continue", ("For", "While", "DoWhile"),
                               f"goto {labels['lupd']}")
            self.curr_lexeme = new[0]
        elif tree[0] == "While":
            labels, new = self.lexing.fancy_rewrite(tree, self.trace,
                    "while (...) ...",
                    "[lchk]: if ({0}) {{ {1} goto [lchk]; }} [lend]: 0;")
            self.replace_stmts(relex(tree)[1:], "Break", ("For", "While", "DoWhile", "Switch"),
                               f"goto {labels['lend']}")
            self.replace_stmts(relex(tree)[1:], "Continue", ("For", "While", "DoWhile"),
                               f"goto {labels['lchk']}")
            self.curr_lexeme = new[0]
        elif tree[0] == "IfStmt":
            if tree[-1][0] == "?": # Have an else branch
                labels, new = self.lexing.fancy_rewrite(tree, self.trace,
                    "if (...) ... else ...",
                    """goto_ite ({0}) [lif] [lelse];
                       [lif]: {{ {1} goto [lend]; }}
                       [lelse]: {2}
                       [lend]: 0;""")
            else:
                labels, new = self.lexing.fancy_rewrite(tree, self.trace,
                    "if (...) ...",
                    "goto_ite ({0}) [lif] [lelse]; [lif]: {{ {1} }} [lelse]: 0;")
            self.curr_lexeme = new[0]
        elif tree[0] == "Block":
            # TODO: Push scope
            self.curr_lexeme = relex(tree[1][2])[0]
        elif tree[0] == "EndBlock":
            # TODO: Pop scope
            self.curr_lexeme = relex(tree)[-1].next_lexeme()
        elif tree[0] == "Line":
            next_lexeme = relex(tree)[-1].next_lexeme()
            tree = parse_some_expr(relex(tree[1][1]))
            self.interpret_expr(tree)
            self.curr_lexeme = next_lexeme
        elif tree[0] == "Switch":
            # Replace the switch itself.
            labels, new = self.lexing.fancy_rewrite(tree, self.trace,
                "switch (...) ...", "auto [val] = ({0}); goto [lend]; {{ {1} }} [lend]: 0;")
            # Now, find goto [lend]
            insert_before = next(l for l in new
                                 if l.string == "goto" and l.next_lexeme().string == labels["lend"])
            # Replace the breaks.
            self.replace_stmts(relex(tree)[1:], "Break", ("For", "While", "DoWhile", "Switch"),
                               f"goto {labels['lend']}")
            # Rewrite cases into labels and goto_ites.
            default_label = labels["lend"]
            fallthrough_label, = self.trace.gen_labels(1)
            for value, case_tree in find_cases(relex(tree[3][1][1][2])):
                label = self.lexing.fancy_rewrite(case_tree, self.trace,
                    "...", "[label]:")[0]["label"]
                if not value:
                    default_label = label
                else:
                    next_label, = self.trace.gen_labels(1)
                    self.lexing.prepend(insert_before,
                        f"{fallthrough_label}: goto_ite ({labels['val']} == ({{value}})) {label} {next_label};",
                        {"value": value})
                    fallthrough_label = next_label
            self.lexing.prepend(insert_before,
                f"{fallthrough_label}: goto {default_label};")
            self.curr_lexeme = new[0]
        else:
            print(tree)
            raise NotImplementedError

    def emit(self, pattern, *args):
        """Wrapper around Trace.emit

        The idea here is that an expression foo() + bar() can emit the IR code
        (add [subtree foo()] [subtree bar()]), and this method will recursively
        call interpret_expr on the arguments [subtree foo()], etc., and produce
        the correct IR that Trace.emit can read.
        """
        expr = parse_expr_str(pattern, True)
        def visit(n):
            if not isinstance(n, list): return
            for i, arg in enumerate(n):
                if arg[0] == "e" and arg[1] == "{" and arg[-1] == "}":
                    n[i] = self.interpret_expr(parse_some_expr(relex(args[int(arg[2:-1])])))
                elif n[i][0] == "{" and n[i][-1] == "}":
                    n[i] = args[int(arg[1:-1])]
                elif isinstance(arg, list):
                    visit(arg)
        visit(expr)
        return self.trace.emit(expr)

    def interpret_expr(self, tree):
        """Wrapper to interpret an expression"""
        with self.trace.explain(relex(tree)):
            return self.interpret_expr_(tree)

    def interpret_expr_(self, tree):
        """Interpreter for expressions"""
        # print("EXPR TREE:", tree)
        if tree[0] == "Member":
            return self.emit("(field e{0} (imm {1}))",
                             tree[1][1], relex(tree)[-1].string)
        elif tree[0] == "Comma":
            lhs = self.interpret_expr(parse_some_expr(relex(tree[1][1])))
            return self.interpret_expr(parse_some_expr(relex(tree[2])))
        elif tree[0] == "Assign":
            if tree[1][2].string != "=":
                assert len(tree[1][2].string) == 2
                op = tree[1][2].string[0]
                # TODO: May double-execute some things?
                _, new = self.lexing.fancy_rewrite(tree, self.trace,
                        f"...{op}=...", f"(({{0}}) = (({{0}}) {op} ({{1}})))")
                return self.interpret_expr(parse_some_expr(new))
            lhs = relex(tree[1][1])
            if lhs[0].label == "ident" and lhs[-1].string == "]" and not any(l.string in (".","->") for l in lhs) and lhs[1].string != "[": # TODO
                # Interpret this as a declaration of an array?
                open_bracket = lhs.index(next(l for l in lhs if l.string == "["))
                lhs = lhs[open_bracket-1:open_bracket]
            elif lhs[0].label == "ident" and lhs[-1].string == "]" and not any(l.string in (".","->") for l in lhs) and lhs[1].string == "[": # TODO
                lhs = lhs
            elif lhs[0].label == "ident" and not any(l.string in (".", "->") for l in lhs): # TODO
                # Interpret this as a declaration?
                lhs = lhs[-1:]
            return self.emit("(upd (* e{0}) e{1})", tree[2][1], lhs)
        elif tree[0] == "Inc":
            # TODO: Pre- vs. Post-inc, also double-execute?
            return self.emit("(upd (+ (imm {0}) (* e{1})) e{1})", 1, tree[1][1])
        elif tree[0] == "Lits":
            literals = tree[1:-1]
            if all(l.label == "strlit" for l in relex(literals)):
                return self.emit("(str (imm {0}))",
                                 "".join([eval(l.string) for l in relex(literals)]))
            if len(literals) != 1:
                return self.interpret_expr(parse_some_expr(relex(tree)[-1:]))
            if literals[0].label == "ident":
                return self.trace.local(literals[0].string)
            if literals[0].label == "numlit":
                lit = literals[0].string
                if all(c.isnumeric() for c in lit):
                    return self.emit("(str (imm {0}))", int(lit))
                return self.emit("(str (imm {0}))", eval(lit))
            raise NotImplementedError
        elif tree[0] == "pre_sizeof":
            _, new = self.lexing.fancy_rewrite(tree, self.trace, "sizeof ...", "sizeof({0})")
            return self.interpret_expr(parse_some_expr(new))
        elif tree[0] == "pre_!":
            _, new = self.lexing.fancy_rewrite(tree, self.trace, "! ...", "(({0}) == 0)")
            return self.interpret_expr(parse_some_expr(new))
        elif tree[0] == "pre_*":
            return self.emit("(* e{0})", tree[2])
        elif tree[0] == "pre_&":
            return self.emit("(str e{0})", tree[2])
        elif tree[0].startswith("pre_"):
            return self.emit("(str ({0} (* e{1})))", tree[0][len("pre_"):], tree[2])
        elif tree[0].startswith("bin_"):
            return self.emit("(str ({0} (* e{1}) (* e{2})))", tree[0], tree[1][1], tree[2])
        elif tree[0] == "Parens":
            return self.interpret_expr(parse_some_expr(relex(tree[1][2])))
        elif tree[0] == "InitList":
            if any(l.string == "return" for l in relex(tree)):
                self.trace.push_scope([], [])
                self.curr_lexeme = relex(tree)[1]
                while True:
                    result = self.step()
                    if isinstance(result, list) and result[0] == "return":
                        self.trace.pop_scope()
                        self.curr_lexeme = relex(tree)[-1].next_lexeme()
                        return result[1]
            fields = list(filter(None, parse_csv(relex(tree)[1:-1], ",")))
            # TODO: something better, top-down
            is_struct = any(f and f[0].string == "." for f in fields)
            if is_struct:
                new, label = ["(", "{"], self.trace.gen_labels(1)[0]
                for field in fields:
                    while len(field) > 1 and field[0].string.startswith("#"):
                        field = field[1:]
                    new += [label, field, ";"]
                new += ["return", label, ";", "}", ")"]
                new = self.lexing.rewrite(relex(tree), new)
                # print(" ".join([l.string for l in new]))
                return self.interpret_expr(parse_some_expr(new))
            else: # parse an array
                new, (label, countlabel) = ["(", "{"], self.trace.gen_labels(2)
                new += [countlabel, "=", "0;"]
                from framework.peg import PEG
                peg = PEG()
                peg.rule("Field", "(? (seq (balanced [ ]) (str =))) (skipto (! (.)))")
                for field in fields:
                    parsed, _ = peg.parse("(: Field)", field)
                    if parsed[1][0] == "?":
                        new += [countlabel, "=", "___ifconcr", "("] + relex(parsed[1])[1:-2] + [",", countlabel, ")", ";"]
                    new += [label, "[", countlabel, "]", "="]
                    new += relex(parsed[-1]) + [";", countlabel, "+=", "1", ";"]
                new += ["return", label, ";", "}", ")"]
                new = self.lexing.rewrite(relex(tree), new)
                # print(" ".join([l.string for l in new]))
                return self.interpret_expr(parse_some_expr(new))
        elif tree[0] == "StructDecl":
            # TODO: A bit sketchy with unnamed fields, but I don't think this
            # is ever actually used anywhere so ...
            fields = []
            for field in parse_csv(relex(tree[-1])[1:-1], ";"):
                if not field: continue
                if field[-1].string == "}":
                    type_ = field
                    name = None
                else:
                    type_ = field[:-1]
                    name = field[-1].string
                if type_ and type_[0].string == "const":
                    type_ = type_[1:]
                if any(l.string == "{" for l in type_):
                    type_ = self.interpret_expr(parse_some_expr(type_))
                else:
                    type_ = None
                fields.append((name, type_))
            # print(fields)
            # TODO: Put it in memory, local ref to it
            return fields
        elif tree[0] == "UnionDecl":
            return None
        elif tree[0] == "EnumDecl":
            options = []
            count = 0
            for field in parse_csv(relex(tree[-1])[1:-1], ","):
                if not field: continue
                name = field[0].string
                if any(l.string == "=" for l in field):
                    count = eval(field[-1].string)
                options.append((name, count))
                count = count + 1
                # TODO: Maybe just insert #defines?
                with self.trace.explain(relex(tree)):
                    local = self.trace.local(name)
                    self.emit("(upd (imm {0}) {1})", count - 1, local)
            return self.emit("(str (imm {0}))", options)
        elif tree[0] == "DerefMember":
            _, new = self.lexing.fancy_rewrite(tree, self.trace,
                    "...->...", "(*({0})).{1}")
            return self.interpret_expr(parse_some_expr(new))
        elif tree[0] == "Nth":
            _, new = self.lexing.fancy_rewrite(tree, self.trace,
                    "...[...]", "(*(({0}) + ({1})))")
            # print(" ".join([l.string for l in new]))
            return self.interpret_expr(parse_some_expr(new))
        elif tree[0] == "FnCall":
            fn = tree[1][1]
            args = parse_csv(relex(tree[1][2][1][2]))
            # print(args, self.trace.scopes)
            if self.is_globals_pass and len(self.trace.scopes) == 1 and len(relex(fn)) > 1:
                # TODO: better detection of declarations vs. calls
                return None
            eval_args = [
                self.interpret_expr(parse_some_expr(relex(arg)))
                for arg in args
            ]
            fn_name = " ".join([l.string for l in relex(fn)])
            if fn_name == "___ifconcr":
                possible = self.emit("(* {0})", eval_args[0])
                return eval_args[0 if possible.canonical.concrete else 1]
            if fn_name in self.fn_handlers:
                # TODO: Maybe we should be passing the lexemes themself? For
                # pass-by-name or smth
                return self.fn_handlers[fn_name](*eval_args)
            if None in self.fn_handlers:
                return self.fn_handlers[None](tree, relex(fn), *eval_args)
            return None
        print(tree)
        raise NotImplementedError

    def returnify_fn(self, start_lexeme):
        """Takes a function and appends a "return;" statement to its body"""
        subtree, _ = PEG().parse("(balanced { })", start_lexeme.suffix(including_self=True))
        assert subtree
        if subtree[-3].string != "return":
            self.lexing.prepend(subtree[-1], "return ;")

    def default_fn_handler(self, tree, fn_lexemes, *args):
        """Handles a function call"""
        if relex(tree)[0].string in self.verbose_fns:
            # If marked verbose, print the arguments
            line_number = relex(tree)[0].line_number
            formats = self.verbose_fns[relex(tree)[0].string]
            try:
                formatted = []
                for a, fmt in zip(args, formats):
                    val = self.trace.emit(("*", a))
                    if val.canonical.concrete:
                        formatted.append(f"{val.cval():{fmt}}")
                    else:
                        formatted.append("[opaque value]")
                print(f"Line {line_number}:",
                        self.lexing.to_string(relex(tree)), "=>",
                        ", ".join(formatted))
            except TypeError:
                for a in args:
                    inner_val = self.trace.emit(("*", a))
                    if isinstance(inner_val.cval(), list):
                        print(f"Line {line_number}: Could not verbose because missing", a.opaque_reason())
        # Try to find a definition of the function
        start_lexeme, param_names = find_fn(fn_lexemes[0])
        if start_lexeme is not False:
            # Found one, make sure it ends in a return statement and start
            # running it!
            self.returnify_fn(start_lexeme)
            old_curr_lexeme = self.curr_lexeme
            # (1) Copy all of the args
            # TODO: Need to actually copy the *contents pointed to* !
            copy_args = [self.emit("(str (* {0}))", a) for a in args]
            self.trace.push_scope(param_names, copy_args)
            self.curr_lexeme = start_lexeme
            while True:
                result = self.step()
                if isinstance(result, list) and result[0] == "return":
                    self.trace.pop_scope()
                    return result[1]
                # TODO: void, or implicit-return functions? Maybe insert an
                # explicit return statement at the end.
            return self.trace.temp(["fneval", relex(tree)])
        # Otherwise, just assume it's opaque
        return self.trace.opaque()
