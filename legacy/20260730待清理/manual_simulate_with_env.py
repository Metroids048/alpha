import os
print('Env vars:')
print(f'   WQ_USERNAME: {os.environ.get("WQ_USERNAME", "NOT_SET")}')

from alpha_mining.factory.orchestrator import FactoryOrchestrator
from alpha_mining.platform.gateway import PlatformGateway
from alpha_mining.simulate.settings_optimizer import SettingsOptimizer

gateway = PlatformGateway(database='alpha_state.sqlite3')
orch = FactoryOrchestrator(database='alpha_state.sqlite3', simulation=gateway)

specs = orch._research_specs()
if specs:
    spec = specs[0]
    candidates = orch.generator.generate(
        hypothesis_id=spec.hypothesis_id,
        family=spec.family,
        fields=spec.fields,
    )
    if not candidates:
        print('No candidates generated')
        exit(1)
    candidate = candidates[0]

    settings = SettingsOptimizer(max_local_trials=4).stage1_default(spec.family)

    print(f'\nTest simulate:')
    print(f'   Expression: {candidate.expression[:60]}...')

    try:
        result = orch.simulation.simulate(
            expression=candidate.expression,
            settings=settings,
            alpha_type='REGULAR'
        )
        print(f'   SUCCESS!')
        print(f'   Alpha ID: {result.alpha_id}')
        print(f'   Status: {result.status}')
        if 'sharpe' in result.metrics:
            print(f'   Sharpe: {result.metrics["sharpe"]}')
    except Exception as e:
        import traceback
        print(f'   FAILED: {type(e).__name__}: {e}')
        traceback.print_exc()
