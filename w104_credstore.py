"""Local coupling to W10.4's fixed synthetic namespace; no deployment entrypoint."""

from w10_observe import UNITS, general_stop_evidence, namespace_quiescent


def confined_manager(manager_type, policy, *, groups, deadline, observer, **kwargs):
    """Use the real manager's guarded API without copying its private sources.

    The caller already owns the closed synthetic window and recorded the current
    stop request. This function never closes/reopens admission or starts services.
    Production identities, source mapping and DB fencing are deliberately absent.
    """
    services = policy.get('services', {})
    if (set(services.get('application_services', ())) != set(UNITS[:5])
            or services.get('operations_check') != 'w9-proof-trigger.service'):
        raise ValueError('only the fixed synthetic namespace is qualified')
    if 'mutation_guard' in kwargs:
        raise ValueError('mutation guard must be the fixed W10.4 proof')

    def guard():
        if not namespace_quiescent(groups, deadline, observer):
            return False
        return general_stop_evidence(deadline)['expected_pre_sigterm'] is True

    return manager_type(policy, mutation_guard=guard, **kwargs)
