"""A slow GPU never mutes the original route; two fast hops permit takeover."""
import ast
from pathlib import Path
import collections
source=(Path(__file__).resolve().parents[1]/'plugins/music/worker.py').read_text(encoding='utf-8')
tree=ast.parse(source);function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='handoff_ready')
scope={'RATE':44100};exec(compile(ast.Module(body=[function],type_ignores=[]),'worker-handoff-guard','exec'),scope)
ready=scope['handoff_ready'];durations=collections.deque(maxlen=2);takeovers=[]
for elapsed in [908,824,475,480]:
    durations.append(elapsed);takeovers.append(ready(durations,28224))
assert takeovers==[False,False,False,True]
assert not ready([575,576],28224)
assert not ready([0],28224)
assert ready([450,475],28224)
assert 'and handoff_ready(hop_times,separator.step)' in source
print('Passed: slow→slow→fast keeps original sound; only fast→fast permits route, strict realtime safety margin preserved.')
