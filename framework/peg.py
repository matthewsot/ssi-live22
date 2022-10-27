"""A simple PEG parser library.

Grammars are specified in an S-expression syntax. Inputs to be parsed are
represented as lists of Lexemes (see lex.py). Resulting trees ar elists of the
form ["label", child1, child2, ...].
"""
class PEG:
    """Represents a parsing expression grammar

    Basic usage:
        peg = PEG()
        peg.rule("label", "(/ (str foo) (str bar))")
        tree, remaining_lexemes = peg.parse("(: label)", lexeme_list)
    """
    def __init__(self):
        """Create a blank PEG"""
        self.rules = dict()

    def rule(self, name, expr):
        """Add a labeled rule to the PEG"""
        expr = parse_expr_str("(" + expr + ")")
        self.rules[name] = expr

    def parse(self, expr, lexemes):
        """Recursive descent PEG parsing"""
        # Some preprocessing...
        if isinstance(expr, str):
            expr = parse_expr_str(expr)
        if expr[0] == "strany":
            expr = ["/"] + list(map(lambda x: ["str", x], expr[1:]))
        # Then actual parsing rules
        if expr[0] == "str":    # string literal
            if lexemes and lexemes[0].string == expr[1]:
                return lexemes[0], lexemes[1:]
            return False, False
        if expr[0] == "::":     # lexeme labeled
            if lexemes and lexemes[0].label == expr[1]:
                return lexemes[0], lexemes[1:]
            return False, False
        if expr[0] == ".":      # any token
            if lexemes:
                return lexemes[0], lexemes[1:]
            return False, False
        if expr[0] in "?":      # optional match
            node = ["?"]
            remainder = lexemes
            for sub in expr[1:]:
                child, remainder = self.parse(sub, remainder)
                if child is False: return None, lexemes
                if child: node.append(child)
            return node, remainder
        if expr[0] == "/":      # any of these
            for sub in expr[1:]:
                node, remainder = self.parse(sub, lexemes)
                if node is not False:
                    return node, remainder
            return False, False
        if expr[0] == "seq":    # sequence of tokens
            node = ["seq"]
            for sub in expr[1:]:
                child, lexemes = self.parse(sub, lexemes)
                if child is False:
                    return False, False
                if child: node.append(child)
            assert lexemes is not False
            return node, lexemes
        if expr[0] == ":":      # recurse into non-terminal
            node = [expr[1]]
            for sub in self.rules[expr[1]]:
                child, lexemes = self.parse(sub, lexemes)
                if child is False:
                    return False, False
                if child: node.append(child)
            assert lexemes is not False
            return node, lexemes
        if expr[0] in ("&", "!"): # positive, negative lookaheads
            node, _ = self.parse(["seq", expr[1]], lexemes)
            if (not node) is (expr[0] == "!"):
                return None, lexemes
            return False, False
        # The next two are less common as PEG primitives. The first is skipto,
        # modified from Brown, N\"otzli, Engler '16. (skipto foo) will skip
        # over tokens lazily until it finds something matching the expression
        # foo. Ours will skip over balanced parentheses as a group:
        # (skipto (str foo)) on "(foo) foo" will skip the entire "(foo)".
        if expr[0] == "skipto": # balanced skipto
            if len(expr) > 2:
                expr = ["skipto", ["seq", *expr[1:]]]
            i = 0
            while i <= len(lexemes):
                skipped_to, remainder = self.parse(expr[1], lexemes[i:])
                if skipped_to is not False:
                    result = ["skipto", lexemes[0:i], skipped_to or []]
                    return result, remainder
                close_i = find_balance(lexemes[i:], "()")
                if close_i is False: close_i = find_balance(lexemes[i:], "{}")
                if close_i is False: close_i = find_balance(lexemes[i:], "[]")

                if close_i is False:
                    i += 1
                else:
                    i += close_i + 1
            return False, False
        # The second simply matches a pair of balanced parens, by default round
        # parens but you can instruct it to use others.
        if expr[0] == "balanced": # match balanced parens
            parens = expr[1:] or ["(", ")"]
            if len(expr) == 2 and expr[1] == "rev":
                parens = [")", "("]
            close_i = find_balance(lexemes, parens)
            if close_i is False:
                return False, False
            result = ["bal", lexemes[0], lexemes[1:close_i], lexemes[close_i]]
            return result, lexemes[(close_i + 1):]
        print(expr)
        raise NotImplementedError

from framework.lex import Lexeme
def filterlex(tree):
    """Given a tree, forms a new tree where node labels are removed.

    E.g., ["label", child1, child2, ...] becomes just [child1, child2, ...]
    """
    newtree = []
    for obj in tree:
        if isinstance(obj, list):
            newtree.append(filterlex(obj))
        elif isinstance(obj, Lexeme):
            newtree.append([obj])
    return list(filter(None, newtree))

def relex(tree):
    """Helper to flatten a tree to a list of its lexemes"""
    if isinstance(tree, Lexeme):
        return [tree]
    if isinstance(tree, list):
        flattened = []
        for child in tree:
            flattened.extend(relex(child))
        return flattened
    return []

EXPR_STR_MEMO = dict()
def parse_expr_str(string, need_copy=False):
    """Memoized S-expr parser"""
    if need_copy: return parse_expr_str_(string, need_copy)
    if string not in EXPR_STR_MEMO:
        EXPR_STR_MEMO[string] = parse_expr_str_(string, need_copy)
    return EXPR_STR_MEMO[string]
def parse_expr_str_(string, need_copy):
    """S-expr parser "(a (b c) d)" -> ["a", ["b", "c"], "d"]

    Note this is pretty simple, and doesn't handle escapes, string literals,
    etc. Just parens and spaces -> list of strings. To make this usable, it
    recognizes "(lparen)" and "(rparen)" and converts them to ["str", "("] and
    ["str", ")"] automatically. TODO: Maybe that should be done by the parser?
    """
    if string == "(lparen)": return ["str", "("]
    if string == "(rparen)": return ["str", ")"]
    if not (string[0] == '(' and string[-1] == ')'):
        return string
    string = string[1:-1]
    expr = []
    while string:
        arg_str = string
        depth = 0
        for i, c in enumerate(string):
            if c == '(': depth += 1
            if c == ')': depth -= 1
            if c in (' ', '\n') and depth == 0:
                arg_str = string[:i]
                break
        expr.append(parse_expr_str(arg_str, need_copy))
        string = string[len(arg_str):].strip()
    return expr

def find_balance(lexemes, parens):
    """Finds a matching parenthesis in a list of lexemes"""
    if not lexemes or lexemes[0].string != parens[0]:
        return False
    depth = 1
    for i in range(1, len(lexemes)):
        if lexemes[i].string == parens[1]:
            depth -= 1
        if lexemes[i].string == parens[0]:
            depth += 1
        if depth == 0:
            return i
    return False
