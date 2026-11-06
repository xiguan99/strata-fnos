import ast, sys, builtins
MODS=set('''functools subprocess os sys json re shutil time pathlib collections itertools hashlib base64 ctypes math queue threading socket struct glob tempfile urllib http signal platform datetime typing dataclasses enum contextlib codecs hmac argparse traceback logging uuid random tarfile zipfile stat errno copy inspect importlib secrets textwrap asyncio concurrent multiprocessing getpass pwd grp pty select fcntl termios shlex string binascii difflib pickle sqlite3 csv io abc weakref gc types warnings'''.split())
BUILT=set(dir(builtins))
class C(ast.NodeVisitor):
    def __init__(s,path): s.path=path; s.problems=[]; s.scopes=[set()]
    def bind(s,n):
        if n: s.scopes[-1].add(n)
    def visit_Import(s,node):
        for a in node.names: s.bind((a.asname or a.name).split('.')[0])
    def visit_ImportFrom(s,node):
        for a in node.names: s.bind(a.asname or a.name)
    def visit_FunctionDef(s,node):
        s.bind(node.name)
        inner=set(); 
        for a in node.args.args+node.args.kwonlyargs+node.args.posonlyargs: inner.add(a.arg)
        if node.args.vararg: inner.add(node.args.vararg.arg)
        if node.args.kwarg: inner.add(node.args.kwarg.arg)
        s.scopes.append(inner); 
        for st in node.body: s.visit(st)
        s.scopes.pop()
    visit_AsyncFunctionDef=visit_FunctionDef
    def visit_ClassDef(s,node):
        s.bind(node.name); s.scopes.append(set())
        for st in node.body: s.visit(st)
        s.scopes.pop()
    def visit_Lambda(s,node):
        inner={a.arg for a in node.args.args+node.args.kwonlyargs+node.args.posonlyargs}
        s.scopes.append(inner); s.visit(node.body); s.scopes.pop()
    def visit_Name(s,node):
        if isinstance(node.ctx,(ast.Store,ast.Del)): s.bind(node.id)
    def visit_Global(s,node):
        for n in node.names: s.bind(n)
    def visit_ExceptHandler(s,node):
        if node.name: s.bind(node.name)
        s.generic_visit(node)
    def visit_comprehension(s,node):
        s.generic_visit(node)
    def visit_Attribute(s,node):
        n=node
        while isinstance(n,ast.Attribute): n=n.value
        if isinstance(n,ast.Name):
            name=n.id
            bound=any(name in sc for sc in s.scopes) or name in BUILT
            if not bound and name in MODS:
                s.problems.append((node.lineno,name))
        s.generic_visit(node)
for p in sys.argv[1:]:
    try: tree=ast.parse(open(p,encoding='utf-8',errors='replace').read(),p)
    except SyntaxError as e: print(f'{p}: SYNTAX {e}'); continue
    v=C(p); v.visit(tree)
    seen=set()
    for ln,nm in v.problems:
        if (nm) in seen: continue
        seen.add(nm); print(f'{p}:{ln}: module `{nm}` used but never bound')
    if not v.problems: print(f'{p}: OK')
