"""Deterministic planner seam - the Plan equivalent of ScriptedClient."""

from browser_agent.planning import Plan, Step

def scripted_planner(plans, tokens=1200):
    it = iter(plans)
    def _p(goal, feedback): return next(it), tokens
    return _p

def S(n, tool, args=None, why="step"):
    return Step(n=n, tool=tool, args=args or {}, why=why)

print("plan_report + planner seam ready")
