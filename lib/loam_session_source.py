"""Compare the Python source that a running session service would load."""
import ast
import io
from pathlib import PurePosixPath
import tokenize

import loam_update as update


def source(rev, path):
    """Read a regular Git blob with its mode. Links, missing paths and errors are opaque."""
    entry = update.git("ls-tree", "--full-tree", "-z", rev, "--", path,
                       check=False, text=False)
    header, sep, name = entry.stdout.rstrip(b"\0").partition(b"\t")
    parts = header.split()
    if (entry.returncode or not sep or name != path.encode() or len(parts) != 3
            or parts[0] not in {b"100644", b"100755"} or parts[1] != b"blob"):
        return None
    blob = update.git("cat-file", "blob", parts[2].decode("ascii"), check=False, text=False)
    if blob.returncode:
        return None
    return parts[0], blob.stdout


def syntax(raw, path, library):
    # ast.parse alone accepts code that cannot run, such as a top-level return.
    compile(raw, path, "exec", dont_inherit=True)
    tree = compile(raw, path, "exec", ast.PyCF_ONLY_AST | ast.PyCF_TYPE_COMMENTS,
                   dont_inherit=True)
    for ignore in tree.type_ignores:
        ignore.lineno = 0
    docs = {}
    if library:
        # Only module documentation and directly callable top-level functions
        # are eligible. Decorators, methods and class creation can consume docs.
        functions = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.setdefault(node.name, []).append(node)
        candidates = [(None, tree)]
        candidates.extend((name, nodes[0]) for name, nodes in functions.items()
                          if len(nodes) == 1 and not nodes[0].decorator_list)
        for name, node in candidates:
            if ast.get_docstring(node, clean=False) is not None:
                docs[name] = node.body.pop(0).value.value
    tokens = tokenize.tokenize(io.BytesIO(raw).readline)
    encoding = next(tokens).string
    shebang = raw.split(b"\n", 1)[0] if raw.startswith(b"#!") else b""
    return (ast.dump(tree), encoding, shebang), docs


def reads_module_filenames(node, parents):
    """Recognize the two non-reflective uses of sys.modules in session diagnostics."""
    method = parents.get(node)
    call = parents.get(method)
    if not isinstance(method, ast.Attribute) or not isinstance(call, ast.Call):
        return False
    if method.attr == "get" and len(call.args) == 1 and isinstance(call.args[0], ast.Constant):
        assign = parents.get(call)
        if (call.args[0].value != "__main__" or not isinstance(assign, ast.Assign)
                or len(assign.targets) != 1 or not isinstance(assign.targets[0], ast.Name)):
            return False
        variable = assign.targets[0].id
        scope = assign
    else:
        sequence = parents.get(call)
        loop = parents.get(sequence)
        if not (method.attr == "values" and isinstance(sequence, ast.Call)
                and isinstance(sequence.func, ast.Name) and sequence.func.id == "list"
                and isinstance(loop, ast.For) and loop.iter == sequence
                and isinstance(loop.target, ast.Name)):
            return False
        variable = loop.target.id
        scope = loop
    # A binding can outlive the statement that introduces it.
    while scope in parents and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        scope = parents[scope]
    for use in ast.walk(scope):
        if isinstance(use, ast.Name) and use.id == variable and isinstance(use.ctx, ast.Load):
            read = parents[use]
            if not (isinstance(read, ast.Call) and isinstance(read.func, ast.Name)
                    and read.func.id == "getattr" and len(read.args) >= 2 and read.args[0] == use
                    and isinstance(read.args[1], ast.Constant) and read.args[1].value == "__file__"):
                return False
    return True


def import_trees(rev, entry, prefix):
    """Read every static runtime import in this Git tree, including multi-name imports."""
    trees = {}
    pending = [prefix + entry]
    while pending:
        path = pending.pop()
        if path in trees:
            continue
        text = update.show(rev, path)
        tree = ast.parse(text) if text is not None else None
        trees[path] = tree
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module] if node.module else []
            else:
                continue
            for module in modules:
                if module.startswith("loam_") and module.isidentifier():
                    pending.append(prefix + "lib/" + module + ".py")
    return trees


def ordinary_definitions(tree):
    # Unknown decorators and metaclasses may inspect siblings at definition time.
    allowed = {a for n in tree.body if isinstance(n, ast.ImportFrom)
               and n.module == "contextlib" and not n.level for a in n.names
               if a.name == "contextmanager" and a.asname is None}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            if n.id == "__name__" or (n.id == "contextmanager" and not isinstance(n.ctx, ast.Load)):
                return False
        if isinstance(n, ast.alias) and (n.asname or n.name) == "contextmanager" and n not in allowed:
            return False
        if getattr(n, "name", None) == "contextmanager" and n not in allowed:
            return False
        if isinstance(n, ast.arg) and n.arg == "contextmanager":
            return False
        if isinstance(n, ast.ClassDef) and (n.keywords or any(
                not isinstance(base, ast.Name) or base.id not in {"object", "Exception", "RuntimeError"}
                for base in n.bases)):
            return False
        for decorator in getattr(n, "decorator_list", ()):
            if not (allowed and isinstance(decorator, ast.Name) and decorator.id == "contextmanager"):
                return False
    return True


def direct_calls_only(trees, owner, names):
    """Trace named imports and literal calls. Treat escapes and reflection as observable."""
    if any(tree is None for tree in trees.values()) or not ordinary_definitions(trees[owner]):
        return False
    module = PurePosixPath(owner).stem
    functions = names - {None}
    namespaces = {module}
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(a.name in functions for a in node.names):
                namespaces.add(PurePosixPath(path).stem)

    # An imported namespace may be re-exported through its importing module.
    aliases = {a.asname or a.name for tree in trees.values() for n in ast.walk(tree)
               if isinstance(n, ast.Import) for a in n.names if a.name in namespaces}
    for path, tree in trees.items():
        local = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names if a.name in namespaces}
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        calls = {n.func for n in parents if isinstance(n, ast.Call)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and n.attr == "modules":
                if not reads_module_filenames(n, parents):
                    return False
            if isinstance(n, ast.Import) and any(
                    a.name.split(".")[0] in {"inspect", "gc", "pydoc", "doctest"} for a in n.names):
                return False
            if isinstance(n, ast.ImportFrom):
                imported = {a.name for a in n.names}
                if (n.module in {"inspect", "gc", "pydoc", "doctest"}
                        or (n.module == module and None in names and "__doc__" in imported)
                        or (n.module == "sys" and "modules" in imported)
                        or (n.module == "importlib" and "import_module" in imported)
                        or (n.module == "builtins" and imported & {
                            "globals", "locals", "vars", "getattr", "hasattr", "eval", "exec",
                            "__import__", "help"})):
                    return False
            if isinstance(n, ast.Name) and n.id in {"getattr", "hasattr"} and n not in calls:
                return False
            if isinstance(n, ast.Name) and n.id in {
                    "__import__", "eval", "exec", "globals", "locals", "vars", "help"}:
                return False
            if isinstance(n, ast.Attribute) and n.attr in {
                    "__doc__", "__dict__", "getdoc", "getmembers", "import_module", "__getattribute__", "_getframe", "f_globals"}:
                return False
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in {
                    "getattr", "hasattr"}:
                if len(n.args) < 2 or not isinstance(n.args[1], ast.Constant):
                    return False
            if isinstance(n, ast.Name) and n.id in local:
                attribute = parents[n]
                if not (isinstance(attribute, ast.Attribute) and attribute.value == n
                        and not attribute.attr.startswith("__")):
                    return False
                use = parents.get(attribute)
                # Direct calls and exception matching consume the named member.
                # Passing other attributes on can expose the namespace to reflection.
                if attribute not in calls and not (isinstance(use, ast.ExceptHandler)
                                                   and use.type == attribute):
                    return False
            if isinstance(n, ast.Attribute) and n.attr in aliases:
                return False
            if isinstance(n, ast.ImportFrom) and any(
                    a.name in namespaces or a.name in aliases or a.name == "*" or (a.name in functions and a.asname)
                    for a in n.names):
                return False
            if isinstance(n, ast.Name):
                if n.id in functions and n not in calls:
                    return False
                if path == owner and None in names and n.id == "__doc__":
                    return False
            if isinstance(n, ast.Attribute) and n.attr in functions and n not in calls:
                return False
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and (
                    n.value == "__doc__" or n.value in functions or n.value in namespaces or n.value in aliases):
                return False
    return True


def same_python(old, target, path, trees, prefix):
    """Skip replacement only for equivalent regular files."""
    try:
        before, after = source(old, path), source(target, path)
        if before is None or after is None or before[0] != after[0]:
            return False
        library = path.startswith(prefix + "lib/") and path.endswith(".py")
        old_syntax, old_docs = syntax(before[1], path, library)
        new_syntax, new_docs = syntax(after[1], path, library)
        if old_syntax != new_syntax:
            return False
        changed_docs = {n for n in old_docs.keys() | new_docs.keys()
                        if old_docs.get(n) != new_docs.get(n)}
        return not changed_docs or (path in trees and direct_calls_only(trees, path, changed_docs))
    except (SyntaxError, UnicodeError, ValueError, LookupError, RecursionError, tokenize.TokenError):
        return False
