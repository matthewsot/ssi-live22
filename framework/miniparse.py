from framework.peg import *

def parse_some_cf(lexemes):
    peg = PEG()

    peg.rule("Block", "(balanced { })")
    peg.rule("EndBlock", "(str })")
    peg.rule("Body", "(/ (: Block) (: Statement))")

    peg.rule("IfStmt", "(str if) (balanced) (: Body) (? (str else) (: Body))")
    peg.rule("DoWhile", "(str do) (: Body) (str while) (balanced) (str ;)")
    peg.rule("While", "(str while) (balanced) (: Body)")
    peg.rule("For", "(str for) (balanced) (: Body)")
    peg.rule("Switch", "(str switch) (balanced) (: Body)")

    peg.rule("Case", "(/ (str case) (str default)) (skipto (str :)) (: Statement)")
    peg.rule("Label", "(:: ident) (str :) (: Statement)")

    peg.rule("Goto", "(str goto) (:: ident) (str ;)")
    peg.rule("GotoITE", "(str goto_ite) (balanced) (:: ident) (:: ident) (str ;)")
    peg.rule("Break", "(str break) (str ;)")
    peg.rule("Continue", "(str continue) (str ;)")
    peg.rule("Return", "(str return) (skipto (str ;))")

    peg.rule("Preproc", "(:: preproc)")

    peg.rule("Quals", "(/ (:: ident) (str *)) (? (: Quals))")
    peg.rule("Function", "(! (/ (str if) (str while) (str for))) (? (: Quals)) (balanced) (! (str ;)) (balanced { })")

    peg.rule("Line", "(skipto (str ;))")

    peg.rule("Statement", "(/ (: IfStmt) (: DoWhile) (: While) (: For) (: Switch) (: Case) (: Label) (: Goto) (: GotoITE) (: Break) (: Continue) (: Return) (: Function) (: Block) (: EndBlock) (: Preproc) (: Line))")

    return peg.parse("(: Statement)", lexemes)

# Adapted from
# https://github.com/pointlander/peg/blob/master/grammars/c/c.peg
def parse_some_expr(lexemes):
    def try_parse(name, rule):
        if try_parse.solution is not None: return
        peg = PEG()
        peg.rule("End", "(! (.))")
        peg.rule(name, rule)
        tree, remainder = peg.parse(f"(: {name})", lexemes)
        if tree is not False and not remainder:
            try_parse.solution = tree
    try_parse.solution = None

    try_parse("Parens", f"(balanced) (: End)")
    try_parse("Lits", f"(/ (:: ident) (:: strlit) (:: numlit)) (? (: Lits)) (: End)")

    try_parse("Comma", "(skipto (str ,)) (skipto (: End))")

    assignops = " ".join(f"(str {op})" for op in "=,*=,/=,%=,+=,-=,<<=,>>=,&=,^=,|=".split(","))
    try_parse("Assign", f"(skipto (/ {assignops})) (skipto (: End))")

    # HMMM: Does this actually work if we have multiple ? Maybe skiptolast ?
    try_parse("Cond", "(skipto (str ?)) (skipto (str :)) (skipto (: End))")

    try_parse("Cast", "(balanced) (& (.)) (/ (& (balanced { })) (! (/ (:: op)))) (skipto (: End))")

    ops = reversed("*,/,%,+,-,<<,>>,<,>,<=,>=,==,!=,|=,&=,&,^,|,&&,||".split(","))
    for op in ops:
        try_parse(f"bin_{op}", f"(! (str {op})) (skipto (str {op})) (skipto (: End))")

    unary_ops = "+,-,++,--,!,~,*,&".split(",")
    for op in unary_ops:
        try_parse(f"pre_{op}", f"(str {op}) (skipto (: End))")
    try_parse(f"pre_sizeof", f"(str sizeof) (! (lparen)) (skipto (: End))")

    try_parse("Nth", f"(skipto (balanced [ ]) (: End))")
    try_parse("Member", f"(skipto (str .) (:: ident) (: End))")
    try_parse("DerefMember", f"(skipto (str ->) (:: ident) (: End))")
    try_parse("Inc", f"(skipto (str ++) (: End))")
    try_parse("Dec", f"(skipto (str ++) (: End))")
    try_parse("FnCall", f"(! (balanced)) (skipto (balanced) (: End))")

    try_parse("Parens", f"(balanced)")

    try_parse("StructDecl", "(str struct) (? (:: ident)) (balanced { })")
    try_parse("EnumDecl", "(str enum) (? (:: ident)) (balanced { })")

    try_parse("InitList", "(balanced { })")

    return try_parse.solution

def parse_csv(lexemes, comma=","):
    peg = PEG()
    peg.rule("Val", f"(skipto (str {comma})) (? (: Val))")
    tree, remainder = peg.parse("(: Val)", lexemes)
    if remainder is False:
        return [lexemes] if lexemes else []
    def visit(node):
        if not isinstance(node, list): return []
        if node[0] == "skipto":
            return [node[1]]
        result = []
        for sub in node:
            result.extend(visit(sub))
        return result
    return visit(tree) + [remainder]

def find_nodes(tree, label):
    if not isinstance(tree, list):
        return []
    if tree[0] == label:
        return [tree]
    result = []
    for child in tree:
        result.extend(find_nodes(child, label))
    return result

def find_cases(lexemes):
    peg = PEG()
    peg.rule("Case", "(/ (str case) (str default)) (skipto (str :))")
    peg.rule("CaseOrSkip", "(/ (: Case) (balanced) (balanced { }) (.)) (? (: CaseOrSkip))")
    matches, remainder = peg.parse("(: CaseOrSkip)", lexemes)
    assert not remainder
    cases = find_nodes(matches, "Case")
    cleaned = []
    for tree in cases:
        if tree[1].string == "case":
            cleaned.append((relex(tree[2][1]), tree))
        else:
            assert tree[1].string == "default"
            cleaned.append(([], tree))
    return cleaned

def find_containing_loop(lexeme):
    all_lexemes = lexeme.all_lexemes
    def unmatched_close(lexemes, parens):
        depth = 0
        for l in lexemes:
            if l.string == parens[0]:
                depth += 1
            if l.string == parens[1]:
                depth -= 1
            if l.string == parens[1] and depth < 0:
                yield l
    after = all_lexemes[all_lexemes.index(lexeme):]
    before = all_lexemes[:all_lexemes.index(lexeme)]
    surrounding_pairs = zip(unmatched_close(before[::-1], ["}", "{"]),
                            unmatched_close(after, ["{", "}"]))
    for start, end in surrounding_pairs:
        start_i = all_lexemes.index(start)
        prefix = all_lexemes[:start_i]
        prefix = prefix[::-1]
        peg = PEG()
        # TODO: switch only sometimes
        peg.rule("ForWhile", "(balanced rev) (/ (str for) (str while) (str switch))")
        peg.rule("Do", "(str do)")
        peg.rule("Loop", "(/ (: ForWhile) (: Do))")
        tree, remainder = peg.parse("(: Loop)", prefix)
        if tree is not False:
            return end
    return None

def find_stmts(lexemes, stmt_types, skip_types):
    results = []
    while lexemes:
        tree, remainder = parse_some_cf(lexemes)
        if tree:
            assert tree[0] == "Statement"
            if tree[1][0] in stmt_types:
                results.append(tree)
            if tree[1][0] in skip_types:
                lexemes = lexemes[lexemes.index(relex(tree)[-1]):]
        lexemes = lexemes[1:]
    return results

find_fn_memo = dict()
def find_fn(name_lex):
    if name_lex.string in find_fn_memo:
        return find_fn_memo[name_lex.string]
    name_lexemes = [l for l in name_lex.lexing.lexemes if l.string == name_lex.string]
    for lexeme in name_lexemes:
        suffix = lexeme.lexing.lexemes[lexeme.lexing.lexemes.index(lexeme):]
        tree, remainder = parse_some_cf(suffix)
        if tree is not False and tree[1][0] == "Function":
            params = [x[-1].string for x in parse_csv(relex(tree[1][2][2])) if x]
            first_lex = relex(tree[1][3][1])[0]
            find_fn_memo[name_lex.string] = first_lex, params
            return first_lex, params
    find_fn_memo[name_lex.string] = False, False
    return False, False

def find_definitions(name, lexemes):
    peg = PEG()
    peg.rule("Ugly", "(? (/ (:: ident) (balanced [ ])) (: Ugly))")
    peg.rule("Def", f"(str {name}) (: Ugly) (str =) (skipto (str ;))")
    results = []
    for i, l in enumerate(lexemes):
        if l.string != name:
            continue
        remaining = lexemes[i:]
        tree, _ = peg.parse("(: Def)", remaining)
        results.append(tree)
    return results

def parse_struct(lexemes):
    if lexemes[0].string != "{" or lexemes[-1].string != "}":
        return None

    parsed = []
    for i, part in enumerate(parse_csv(lexemes[1:-1])):
        if not part:
            continue
        name = None
        if part[0].string == ".":
            name = part[1].string
            part = part[3:]
        parsed.append((name, part))
    return parsed

def parse_macro(string):
    from framework.lex import lex_c
    string = string.replace("\\\n", " ")
    if not string.startswith("#define "):
        return None
    string = string[len("#define "):].strip()
    lexemes = lex_c(string).lexemes
    macro = dict({"name": lexemes[0].string, "args": None, "pattern": []})
    if lexemes[1].string == "(":
        macro["args"] = []
        for i, key in enumerate(lexemes[2:]):
            if key.string == ")": break
            if key.string == ",": continue
            macro["args"].append(key.string)
        lexemes = lexemes[2+i:]
    args = macro["args"] or []
    for lexeme in lexemes[1:]:
        if lexeme.string[0] == "#" and lexeme.string[1:] in args:
            macro["pattern"].append(("strify", args.index(lexeme.string[1:])))
        elif lexeme.string in args:
            macro["pattern"].append(args.index(lexeme.string))
        else:
            macro["pattern"].append(lexeme.string)
    return macro
