import ast, sys
p = r'app/gui/bom_editor_pane.py'
try:
    with open(p, 'r', encoding='utf-8') as f:
        src = f.read()
    ast.parse(src)
    print('AST OK')
except SyntaxError as e:
    print('SYNTAX ERROR:', e)
    print('Line:', e.lineno)
    print('Text:', src.splitlines()[e.lineno-1])
