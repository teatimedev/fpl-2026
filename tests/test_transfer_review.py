from v2.transfer_review import stress_player


def test_double_gameweek_stress_scales_linearly_in_fixture_count():
    player = dict(pos='FWD', team='A', proj_by_gw=[10.], mins_by_gw=[90.],
                  xg90=1., xa90=0.)
    single = stress_player(player, {'A': {'1': [{'xg': 1.45}]}}, 1, 1, attack_drop=.2)
    double = stress_player({**player, 'proj_by_gw': [20.], 'mins_by_gw': [180.]},
                           {'A': {'1': [{'xg': 1.45}, {'xg': 1.45}]}}, 1, 1, attack_drop=.2)
    assert abs(double['proj_by_gw'][0] - 2 * single['proj_by_gw'][0]) < 1e-9
