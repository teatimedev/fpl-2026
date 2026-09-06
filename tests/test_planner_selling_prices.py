"""The original holding earns half its rise; a repurchase is a new lot."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'v2'))
from planner import plan


def pool():
    players = {}
    for pos,count in [('GKP',2),('DEF',5),('MID',5),('FWD',3)]:
        for _ in range(count):
            pid = len(players)+1
            players[pid] = dict(id=pid,name=f'P{pid}',pos=pos,team=f'T{pid}',
                                price=4.5,status='a',proj_by_gw=[8.]*9,
                                play_by_gw=[1.]*9)
    owned = list(players)
    players[8]['price'] = 5.2
    players[8]['proj_by_gw'] = [50. if g%2 else 0. for g in range(1,10)]
    players[16] = dict(players[8],id=16,name='Replacement',team='T16',price=5.1,
                       proj_by_gw=[0. if g%2 else 50. for g in range(1,10)])
    return players,owned


class SellingPricesTests(unittest.TestCase):
    def test_unsold_profit_does_not_make_holding_infeasible(self):
        players,owned = pool()
        result = plan(players,owned,0,1,4,4,freeze_this_week=True,sell_prices={8:5.1})
        self.assertIsNotNone(result)
        self.assertEqual(set(result['weeks'][0]['squad']),set(owned))

    def test_cannot_spend_unrealised_half_of_price_rise(self):
        players,owned = pool()
        players[16]['price'] = 5.2
        legacy = plan(players,owned,0,1,4,4)
        actual = plan(players,owned,0,1,4,4,sell_prices={8:5.1})
        self.assertIn(16,legacy['weeks'][0]['squad'])
        self.assertNotIn(16,actual['weeks'][0]['squad'])

    def test_repurchase_costs_full_price(self):
        players,owned = pool()
        players[16]['proj_by_gw'][3] = 80.
        result = plan(players,owned,0,1,4,5,sell_prices={8:5.1})
        self.assertIn(16,result['weeks'][0]['squad'])
        self.assertNotIn(8,result['weeks'][1]['squad'])

    def test_repurchase_does_not_charge_original_discount_twice(self):
        players,owned = pool()
        result = plan(players,owned,.1,1,4,7,sell_prices={8:5.1})
        for wk,target in zip(result['weeks'],[16,8,16,8]):
            self.assertIn(target,wk['squad'])
            self.assertEqual(wk['hits'],0)

    def test_invalid_sale_price_rejected(self):
        players,owned = pool()
        with self.assertRaises(ValueError):
            plan(players,owned,0,1,4,4,sell_prices={8:5.3})

    def test_forced_single_uses_real_budget_and_transfer_bank(self):
        players, owned = pool()
        chosen = [16 if i == 8 else i for i in owned]
        result = plan(players, owned, 0, 2, 4, 5, sell_prices={8:5.1}, first_week_squad=chosen)
        self.assertEqual(set(result['weeks'][0]['squad']), set(chosen))
        self.assertEqual(result['weeks'][0]['in'], [16])
        self.assertEqual(result['weeks'][1]['ft'], 2)
        players[16]['price'] = 5.3
        self.assertIsNone(plan(players, owned, 0, 2, 4, 5, sell_prices={8:5.1}, first_week_squad=chosen))


if __name__=='__main__':
    unittest.main()
