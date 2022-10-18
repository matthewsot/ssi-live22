import types

def filterlex(tree):
    from framework.lex import Lexeme
    newtree = []
    for obj in tree:
        if isinstance(obj, list):
            newtree.append(filterlex(obj))
        elif isinstance(obj, Lexeme):
            newtree.append([obj])
    return list(filter(None, newtree))

def relex(tree):
    from framework.lex import Lexeme
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
    if need_copy: return parse_expr_str_(string, need_copy)
    if string not in EXPR_STR_MEMO:
        EXPR_STR_MEMO[string] = parse_expr_str_(string, need_copy)
    return EXPR_STR_MEMO[string]
def parse_expr_str_(string, need_copy):
    if string == "(lparen)": return ["str", "("]
    if string == "(rparen)": return ["str", ")"]
    if not (string[0] == '(' and string[-1] == ')'):
        return string
    string = string[1:-1]
    expr = []
    while string:
        arg_str = string
        depth = 0
        for i in range(len(string)):
            if string[i] == '(': depth += 1
            if string[i] == ')': depth -= 1
            if string[i] in (' ', '\n') and depth == 0:
                arg_str = string[:i]
                break
        expr.append(parse_expr_str(arg_str, need_copy))
        string = string[len(arg_str):].strip()
    return expr

def find_balance(lexemes, parens):
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

class PEG:
    def __init__(self):
        self.rules = dict()

    def rule(self, name, expr):
        if isinstance(expr, str): expr = parse_expr_str("(" + expr + ")")
        self.rules[name] = expr

    def parse(self, expr, lexemes):
        if isinstance(expr, types.FunctionType): return expr(lexemes)
        if isinstance(expr, str): expr = parse_expr_str(expr)
        if expr[0] == "strany":
            expr = ["/"] + list(map(lambda x: ["str", x], expr[1:]))
        if expr[0] == "str":
            if lexemes and lexemes[0].string == expr[1]:
                return lexemes[0], lexemes[1:]
            return False, False
        if expr[0] == "::":
            if lexemes and lexemes[0].label == expr[1]:
                return lexemes[0], lexemes[1:]
            return False, False
        if expr[0] == ".":
            if lexemes:
                return lexemes[0], lexemes[1:]
            return False, False
        if expr[0] in "?":
            node = ["?"]
            remainder = lexemes
            for sub in expr[1:]:
                child, remainder = self.parse(sub, remainder)
                if child is False: return None, lexemes
                if child: node.append(child)
            return node, remainder
        if expr[0] == "/":
            for sub in expr[1:]:
                node, remainder = self.parse(sub, lexemes)
                if node is not False:
                    return node, remainder
            return False, False
        if expr[0] == "seq":
            node = ["seq"]
            for sub in expr[1:]:
                child, lexemes = self.parse(sub, lexemes)
                if child is False:
                    return False, False
                if child: node.append(child)
            assert lexemes is not False
            return node, lexemes
        if expr[0] == ":":
            if isinstance(self.rules[expr[1]], types.FunctionType):
                return self.rules[expr[1]](lexemes)
            node = [expr[1]]
            for sub in self.rules[expr[1]]:
                child, lexemes = self.parse(sub, lexemes)
                if child is False:
                    return False, False
                if child: node.append(child)
            assert lexemes is not False
            return node, lexemes
        if expr[0] in ("&", "!"):
            node, _ = self.parse(["seq", expr[1]], lexemes)
            if (not node) is (expr[0] == "!"):
                return None, lexemes
            return False, False
        if expr[0] == "skipto":
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
        if expr[0] == "balanced":
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
