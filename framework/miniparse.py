"""Grammars and other parsing tools for C code

The expression grammar was originally based off of
https://github.com/pointlander/peg/blob/master/grammars/c/c.peg
"""
from framework.peg import PEG, relex

def parse_some_cf(lexemes):
    """Parser C control flow with as many holes as possible.

    Unfortunately, even an approximate C control flow grammar is pretty
    difficult to fit in to the "only parse what you need" mantra, because
    bodies of, e.g., an if statement can be just "another statement". So if the
    body of the if contains some construct we don't understand yet, things may
    break. Currently we just do a standard recursive parsing with holes, but
    that can probably be improved (e.g., skip to a ;).

    Notably, though, this is still extremely simple to extend --- just add a
    new syntactic construct to the list in peg.rule("Statement").
    """
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

def parse_some_expr(lexemes):
    """Parses a C expression (with holes)

    This parser is very compositional; we simply iterate down rules by
    precedence until we find one that matches the whole string. These rules
    make liberal use of skipto and balanced so they never need to recurse.

    Currently, this is the performance bottleneck for our interpreter. There
    are a few ways to speed this up; an obvious one is over-approximating rules
    and pre-matching the overapproximation. Or trying something like Pratt
    parsing. But it's not a huge focus for this prototype.

    There's also probably a lot of precedence/associativity issues in
    here...But, again, the goal is to be "good enough for most code," not
    "perfect."
    """
    def try_parse(name, rule):
        """If a solution has not been found, try parsing against @rule"""
        if try_parse.solution is not None:
            return
        peg = PEG()
        peg.rule("End", "(! (.))")
        peg.rule(name, rule)
        tree, remainder = peg.parse(f"(: {name})", lexemes)
        if tree is not False and not remainder:
            try_parse.solution = tree
    try_parse.solution = None

    try_parse("Parens", "(balanced) (: End)")
    try_parse("Lits", "(/ (:: ident) (:: strlit) (:: numlit)) (? (: Lits)) (: End)")

    try_parse("Comma", "(skipto (str ,)) (skipto (: End))")

    assignops = " ".join(f"(str {op})" for op in "=,*=,/=,%=,+=,-=,<<=,>>=,&=,^=,|=".split(","))
    try_parse("Assign", f"(skipto (/ {assignops})) (skipto (: End))")

    # TODO: Does this actually work if we have multiple ? Maybe skiptolast ?
    try_parse("Cond", "(skipto (str ?)) (skipto (str :)) (skipto (: End))")

    try_parse("Cast", "(balanced) (& (.)) (/ (& (balanced { })) (! (/ (:: op)))) (skipto (: End))")

    ops = reversed("*,/,%,+,-,<<,>>,<,>,<=,>=,==,!=,|=,&=,&,^,|,&&,||".split(","))
    for op in ops:
        try_parse(f"bin_{op}", f"(! (str {op})) (skipto (str {op})) (skipto (: End))")

    unary_ops = "+,-,++,--,!,~,*,&".split(",")
    for op in unary_ops:
        try_parse(f"pre_{op}", f"(str {op}) (skipto (: End))")
    try_parse("pre_sizeof", "(str sizeof) (! (lparen)) (skipto (: End))")

    try_parse("Nth", "(skipto (balanced [ ]) (: End))")
    try_parse("Member", "(skipto (str .) (:: ident) (: End))")
    try_parse("DerefMember", "(skipto (str ->) (:: ident) (: End))")
    try_parse("Inc", "(skipto (str ++) (: End))")
    try_parse("Dec", "(skipto (str ++) (: End))")
    try_parse("FnCall", "(! (balanced)) (skipto (balanced) (: End))")

    try_parse("Parens", "(balanced)")

    try_parse("StructDecl", "(str struct) (? (:: ident)) (balanced { })")
    try_parse("EnumDecl", "(str enum) (? (:: ident)) (balanced { })")

    try_parse("InitList", "(balanced { })")

    return try_parse.solution

def parse_csv(lexemes, comma=","):
    """"foo, bar" -> ["foo", "bar"]"""
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
    """Filters the tree to only nodes labeled @label"""
    if not isinstance(tree, list):
        return []
    if tree[0] == label:
        return [tree]
    result = []
    for child in tree:
        result.extend(find_nodes(child, label))
    return result

def find_cases(lexemes):
    """Parses the body of a switch for case statements"""
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

def find_stmts(lexemes, stmt_types, skip_types):
    """Recursively parses @lexemes and returns those of type @stmt_types

    Statements with type in @skip_types are not recursed into.

    This is used, e.g., to identify all the "break" statements within a "while"
    statement so they can be replaced with gotos to the exit of the while. We
    use @skip_types to tell it not to recurse into sub-while statements,
    because their "break"s should goto a different location.
    """
    results = []
    while lexemes:
        tree, _ = parse_some_cf(lexemes)
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
    """Find a function declaration with the given name"""
    key = (name_lex.lexing, name_lex.string)
    if key in find_fn_memo:
        return find_fn_memo[key]
    name_lexemes = [l for l in name_lex.lexing.lexemes
                    if l.string == name_lex.string]
    for lexeme in name_lexemes:
        suffix = lexeme.lexing.lexemes[lexeme.lexing.lexemes.index(lexeme):]
        tree, _ = parse_some_cf(suffix)
        if tree is not False and tree[1][0] == "Function":
            params = [x[-1].string for x in parse_csv(relex(tree[1][2][2])) if x]
            first_lex = relex(tree[1][3][1])[0]
            find_fn_memo[key] = first_lex, params
            return first_lex, params
    find_fn_memo[key] = False, False
    return False, False

def parse_macro(string):
    """Parse a C macro given its string representation"""
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
