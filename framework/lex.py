import re
from framework.peg import relex

def path_to_string(path):
    with open(path, "r") as file:
        return "".join(file.readlines())

class Lexeme:
    def __init__(self, lexing, label, start_idx, length, pseudo_string=None):
        self.lexing = lexing
        self.label = label
        self.start_idx = start_idx
        self.end_idx = start_idx + length
        if pseudo_string:
            self.pseudo = True
            self.string = pseudo_string
        else:
            self.pseudo = False
            self.string = lexing.full_string[start_idx:self.end_idx]

    @property
    def line_number(self):
        return 1 + self.lexing.full_string[:self.start_idx].count("\n")

    def prev_lexeme(self):
        if self.lexing.lexemes[0] == self: return None
        return self.lexing.lexemes[self.lexing.lexemes.index(self) - 1]

    def next_lexeme(self):
        if self.lexing.lexemes[-1] == self: return None
        return self.lexing.lexemes[self.lexing.lexemes.index(self) + 1]

    def suffix(self, incl=False):
        if self.lexing.lexemes[-1] == self:
            return []
        idx = self.lexing.lexemes.index(self)
        if not incl: idx += 1
        return self.lexing.lexemes[idx:]

    def __repr__(self):
        return f"{self.string}: {self.label}"

class Lexing:
    def __init__(self, rules, full_string):
        self.rules = rules
        self.full_string = full_string
        self.lexemes = []

    def to_string(self, lexemes):
        return self.full_string[lexemes[0].start_idx:lexemes[-1].end_idx]

    def fancy_rewrite(self, tree, trace, old_pattern, new_pattern):
        from framework.peg import filterlex
        tree = filterlex(tree)
        old_range = relex(tree)
        # Find pieces from the old pattern
        old_pattern = list(filter(None, old_pattern.replace("...", " ... ").split(" ")))
        def lsa(subtree, string):
            if isinstance(subtree, Lexeme):
                raise NotImplementedError
            elif len(relex(subtree)) == 1 and relex(subtree)[0].string == string:
                return []
            else:
                remainder = lsa(subtree[0], string)
                return list(filter(None, [remainder] + subtree[1:]))
        def lsa_suffix(subtree, string):
            if string is None:
                return relex(subtree), []
            if isinstance(subtree, Lexeme):
                raise NotImplementedError
            for i, child in enumerate(subtree):
                if relex(child)[0].string == string:
                    return relex(subtree[:i]), subtree[i:]
            lexemes, remainder = lsa_suffix(subtree[0], string)
            return lexemes, list(filter(None, [remainder] + subtree[1:]))
        pieces = []
        for part, next_part in zip(old_pattern, old_pattern[1:] + [None]):
            if part == "...":
                piece, tree = lsa_suffix(tree, next_part)
                pieces.append(piece)
            else:
                tree = lsa(tree, part)
        # Generate labels for the new pattern
        labels = [x[1:-1] for x in sorted(set(re.findall(r"\[.*?\]", new_pattern)))]
        labels = dict(zip(labels, trace.gen_labels(len(labels))))
        for label in labels:
            new_pattern = new_pattern.replace(f"[{label}]", "{" + label + "}")
        labels.update(dict({
            str(i): piece for i, piece in enumerate(pieces)
        }))
        return labels, self.rewrite(old_range, new_pattern, labels)

    def rewrite(self, old_range, pattern, substitutions=None, inclusive=True):
        if isinstance(pattern, list):
            assert substitutions is None
            substitutions = dict()
            new_pattern = ""
            for arg in pattern:
                if isinstance(arg, str):
                    new_pattern += " " + arg.replace("{", "{{").replace("}", "}}")
                    continue
                if isinstance(arg, Lexeme):
                    arg = [arg]
                assert isinstance(arg, list) and isinstance(arg[0], Lexeme)
                for subarg in arg:
                    new_pattern += " " + f"{{{len(substitutions)}}}"
                    substitutions[str(len(substitutions))] = [subarg]
            return self.rewrite(old_range, new_pattern, substitutions, inclusive)
        parts = Lexing.lex(dict({
            "Literal": "([{][{])|([}][}])",
            "String": r"[^{}]*",
            "Sub": r"[{][^{}]*?[}]",
        }), pattern).lexemes
        new_lexemes = []
        for part in parts:
            if part.label == "Literal":
                part.string = part.string[0]
                part.label = "String"

            if part.label == "Sub" and isinstance(substitutions[part.string[1:-1]], str):
                part.label = "String"
                part.string = substitutions[part.string[1:-1]]

            if part.label == "String":
                lexed = Lexing.lex(self.rules, part.string).lexemes
                idx = old_range[-1].start_idx
                if new_lexemes:
                    idx = new_lexemes[-1].start_idx
                for lex in lexed:
                    lex.start_idx = idx
                    lex.end_idx = idx + 1
                new_lexemes.extend(lexed)
            elif part.label == "Sub":
                new_lexemes.extend(substitutions[part.string[1:-1]])
        start = old_range[0]
        if inclusive:
            end = old_range[-1].next_lexeme()
        else:
            end = old_range[-1]
        prefix = self.lexemes[:self.lexemes.index(start)]
        suffix = self.lexemes[self.lexemes.index(end):]
        self.lexemes = prefix + new_lexemes + suffix
        for lex in new_lexemes:
            lex.lexing = self
        return new_lexemes

    def prepend(self, before, pattern, substitutions=None):
        return self.rewrite([before], pattern, substitutions, inclusive=False)

    def after_line_number(self, line_number):
        return [l for l in self.lexemes if l.line_number >= line_number]

    def clone(self):
        import copy
        return copy.deepcopy(self)

    # this is terrible, use lex to get O(N) instead of O(N^2)
    @staticmethod
    def lex(rules, string):
        self = Lexing(rules, string)
        compiled = [(name, re.compile(rule, re.DOTALL)) for name, rule in rules.items()]
        full_string = string
        start_idx = 0
        while string:
            longest_name, longest_len = None, 0
            for name, prog in compiled:
                result = prog.match(string)
                assert not result or result.start() == 0
                if result and longest_len < result.end():
                    longest_len = result.end()
                    longest_name = name
            # print(string[:longest_len])
            # if not longest_len:
            #     print("BAD:", string[:10])
            assert longest_len
            if longest_name[0] != "_":
                self.lexemes.append(
                    Lexeme(self, longest_name, start_idx, longest_len))
            start_idx += longest_len
            string = string[longest_len:]
        return self

def lex_c(string):
    return Lexing.lex({
        "preproc": r'^#[a-zA-Z_]+([^\n]|[\\][\n])*?\n',
        "op": r"[\-][>]|\+\+|<<|>>|--|==|&&|[<>!+\-*/&|](=?)|[,(){};.=:&|~%?]|\[|\]",
        "ident": "[a-zA-Z_][a-zA-Z0-9_]*",
        "strify": r'#[a-zA-Z_]+',
        "numlit": "(0x[0-9a-fA-F]*)|([0-9]*)",
        "strlit": r'["]([\\]["]|[^"][^"])*[^"]?["]',
        "chrlit": r"[']([\\][']|[^'][^'])*[^']?[']",
        "_slc": r'//[^\n]*',
        "_mlc": r'/[*]((?![*]/).)*[*]/',
        "_space": r"\s",
    }, string)
