"""Lexing module.

Goal is to take a string and produce a sequence of lexemes (here == tokens).

The sequence of lexemes is wrapped in a Lexing object, which from here on out
represents the source code. When later stages do desugaring, etc., they do it
directly on the Lexing object, which provides methods like fancy_rewrite(...)
to assist with this.
"""
import re

class Lexeme:
    """Represents a single lexeme in the file"""
    def __init__(self, lexing, label, start_idx, length, pseudo_string=None):
        """Create a new Lexeme at the specified location in the file.

        Should only be called from the Lexing class.  If @pseudo_string is not
        None, we assume this lexeme is coming from a desugaring/rewrite, *not*
        from the file itself.
        """
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
        """Line number in the original file"""
        return 1 + self.lexing.full_string[:self.start_idx].count("\n")

    def next_lexeme(self):
        """Next lexeme in the lexing"""
        if self.lexing.lexemes[-1] == self:
            return None
        return self.lexing.lexemes[self.lexing.lexemes.index(self) + 1]

    def suffix(self, including_self=False):
        """List all lexemes after(/including) @self"""
        if self.lexing.lexemes[-1] == self:
            return []
        idx = self.lexing.lexemes.index(self)
        if not including_self:
            idx += 1
        return self.lexing.lexemes[idx:]

    def __repr__(self):
        """Pretty-print"""
        return f"{self.string}: {self.label}"

class Lexing:
    """Represents a source file as a list of lexemes"""
    def __init__(self, rules, full_string):
        """Initialize a lexing. Should only be called via Lexing.lex."""
        self.rules = rules
        self.full_string = full_string
        self.lexemes = []

    def to_string(self, lexemes):
        """Lexeme range -> original string"""
        return self.full_string[lexemes[0].start_idx:lexemes[-1].end_idx]

    # TODO: this is terrible, use lex to get O(N) instead of O(N^2)
    @staticmethod
    def lex(rules, string):
        """Lex an input file with the given rules.

        @rules is a dict of {token_label: regex_str} entries. If token_label
        begins with an underscore, it is treated as a comment/whitespace token
        and not included in the final lexing. Returns a Lexing object.
        """
        self = Lexing(rules, string)
        compiled = [(name, re.compile(rule, re.DOTALL))
                    for name, rule in rules.items()]
        start_idx = 0
        while string:
            longest_name, longest_len = None, 0
            for name, prog in compiled:
                result = prog.match(string)
                assert not result or result.start() == 0
                if result and longest_len < result.end():
                    longest_len = result.end()
                    longest_name = name
            assert longest_len
            if longest_name[0] != "_":
                self.lexemes.append(
                    Lexeme(self, longest_name, start_idx, longest_len))
            start_idx += longest_len
            string = string[longest_len:]
        return self

    def rewrite(self, old_range, pattern, substitutions=None, inclusive=True):
        """Core rewriter module.

        @old_range is [start, end] specifying the range of lexemes to replace.
        @inclusive determines if end is replaced or not.

        @pattern is a format string-style pattern. Every substring like {a} in
        the @pattern is replaced with @substitutions[a], which can be a string
        or a list of lexemes. Substitutions can be escaped like {{a}} to get
        a literal string {a}.

        Returns the new lexemes inserted in place of @old_range.
        """
        # We also support giving the pattern as a list, which is automatically
        # transformed into a pattern string.
        if isinstance(pattern, list):
            assert substitutions is None
            substitutions = dict()
            new_pattern = ""
            for arg in pattern:
                if isinstance(arg, str):
                    new_pattern += " "
                    new_pattern += arg.replace("{", "{{").replace("}", "}}")
                    continue
                if isinstance(arg, Lexeme):
                    arg = [arg]
                assert isinstance(arg, list) and isinstance(arg[0], Lexeme)
                for subarg in arg:
                    new_pattern += " " + f"{{{len(substitutions)}}}"
                    substitutions[str(len(substitutions))] = [subarg]
            return self.rewrite(old_range, new_pattern,
                                substitutions, inclusive)

        # Parse the pattern string.
        parts = Lexing.lex(dict({
            "Literal": "([{][{])|([}][}])",
            "String": r"[^{}]*",
            "Sub": r"[{][^{}]*?[}]",
        }), pattern).lexemes
        # Fill in the pattern string to identify what the new lexemes to fill
        # in are.
        new_lexemes = []
        for part in parts:
            if part.label == "Literal":
                part.label = "String"
                part.string = part.string[0]

            if (part.label == "Sub"
                    and isinstance(substitutions[part.string[1:-1]], str)):
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
        # Then actually do the replacement in the list of lexemes.
        start = old_range[0]
        end = old_range[-1]
        if inclusive:
            end = end.next_lexeme()
        prefix = self.lexemes[:self.lexemes.index(start)]
        suffix = self.lexemes[self.lexemes.index(end):]
        self.lexemes = prefix + new_lexemes + suffix
        for lex in new_lexemes:
            lex.lexing = self
        return new_lexemes

    def fancy_rewrite(self, tree, trace, old_pattern, new_pattern):
        """Similar to rewrite, except bases substitutions on existing parse

        For example, if you already have a parse tree for if (x != y) foo;
        this allows you to specify rewrite patterns like:
        if ( ... ) ...
        ->
        goto_ite {0} [l_if] [l_else]; [l_if]: {1} [l_else]: 0;
        """
        from framework.peg import filterlex, relex
        tree = filterlex(tree)
        old_range = relex(tree)
        # Find pieces from the old pattern. The basic idea is to walk the tree,
        # looking for the first subtree that match the ....
        # TODO: These operations are a bit unnecessarily confusing; worth
        # rewriting.
        old_pattern = old_pattern.replace("...", " ... ").split(" ")
        old_pattern = [x for x in old_pattern if x]
        def lsa(subtree, string):
            """Form a new tree left-stripping @string

            The basic idea is if we're matching if ( ... ) ..., we take the
            parse tree and remove (with this function) the first subtree that
            corresponds to "if".
            """
            assert not isinstance(subtree, Lexeme)
            relexed = relex(subtree)
            if len(relexed) == 1 and relexed[0].string == string:
                return []
            remainder = lsa(subtree[0], string)
            return list(filter(None, [remainder] + subtree[1:]))
        def lsa_suffix(subtree, string):
            """Left-strip a tree until we see @string

            The idea is to find the least-deep, leftmost-subtree sequence that
            is followed by a lexeme of @string.
            """
            if string is None:
                return relex(subtree), []
            if isinstance(subtree, Lexeme):
                raise NotImplementedError
            for i, child in enumerate(subtree):
                if relex(child)[0].string == string:
                    return relex(subtree[:i]), subtree[i:]
            lexemes, remainder = lsa_suffix(subtree[0], string)
            return lexemes, list(filter(None, [remainder] + subtree[1:]))
        # For a pattern like if (...) ..., we use lsa to strip off a
        # leftmost-subtree for "if", then for "(", then lsa_suffix to read
        # subtrees until we see a ")", etc.
        pieces = []
        for part, next_part in zip(old_pattern, old_pattern[1:] + [None]):
            if part == "...":
                piece, tree = lsa_suffix(tree, next_part)
                pieces.append(piece)
            else:
                tree = lsa(tree, part)

        # Anywhere we see [label] in the @new_pattern, we need to generate a
        # fresh label and insert it.
        labels = [x[1:-1]
                  for x in sorted(set(re.findall(r"\[.*?\]", new_pattern)))]
        labels = dict(zip(labels, trace.gen_labels(len(labels))))
        for label in labels:
            new_pattern = new_pattern.replace(f"[{label}]", "{" + label + "}")
        labels.update(dict({
            str(i): piece for i, piece in enumerate(pieces)
        }))
        return labels, self.rewrite(old_range, new_pattern, labels)

    def prepend(self, before, pattern, substitutions=None):
        """Helper to insert lexemes before a certain node"""
        return self.rewrite([before], pattern, substitutions, inclusive=False)

    def after_line_number(self, line_number):
        """Returns all lexemes after a given line number"""
        return [l for l in self.lexemes if l.line_number >= line_number]

def lex_c(string):
    """C lexer"""
    return Lexing.lex({
        "preproc": r'^#[a-zA-Z_]+([\\][\n]|[^\n])*?\n',
        "op": r"[\-][>]|\+\+|<<|>>|--|==|&&|[<>!+\-*/&|](=?)|[,(){};.=:&|~%?]|\[|\]|\^",
        "ident": "[a-zA-Z_][a-zA-Z0-9_]*",
        "strify": r'#[a-zA-Z_]+',
        "pasteify": r'##[a-zA-Z_]+',
        "numlit": "(0x[0-9a-fA-F]*)|([0-9]*)",
        "strlit": r'["]([\\]["]|[^"][^"])*[^"]?["]',
        "chrlit": r"[']([\\][']|[^'][^'])*[^']?[']",
        "_slc": r'//[^\n]*',
        "_mlc": r'/[*]((?![*]/).)*[*]/',
        "_space": r"\s",
    }, string)

def path_to_string(path):
    """Helper to read a file"""
    return "".join(open(path, "r").readlines())
