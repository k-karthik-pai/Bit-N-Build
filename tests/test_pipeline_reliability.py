"""Regression tests for real failures found after integration."""
import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from agents.negotiation import negotiate, validate_proposal, validate_quantity
from agents.logistics import optimize_routes, get_route_cost
from orchestrator import run_pipeline

ROOT = Path(__file__).resolve().parents[1]


class ReliabilityTests(unittest.TestCase):
    def test_llm_placeholders_and_fallback(self):
        seller = {'min_acceptable_price_per_tonne_usd': 22,
                  'preferred_price_per_tonne_usd': 25, 'available_quantity_tonnes': 1000}
        buyer = {'buyer_id': 'test', 'max_acceptable_price_per_tonne_usd': 28,
                 'annual_demand_tonnes': 100}
        baseline = negotiate(seller, buyer, use_llm=False)
        for mode in ('valid', 'number_injection', 'missing_token', 'wrong_speaker', 'failure'):
            calls = []
            def create(**kwargs):
                calls.append(kwargs)
                if mode == 'failure':
                    raise RuntimeError('mock network failure')
                transcript = json.loads(kwargs['messages'][1]['content'])['transcript']
                if mode == 'number_injection':
                    transcript[0]['message'] += ' Charge $999.'
                elif mode == 'missing_token':
                    transcript[0]['message'] = 'Omitted terms.'
                elif mode == 'wrong_speaker':
                    transcript[0]['speaker'] = 'buyer_agent'
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                    content=json.dumps({'transcript': transcript})))])
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            with self.subTest(mode=mode), patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=lambda **kw: client)}), \
                    patch('agents.negotiation._resolve_llm_config', return_value=({'api_key': 'fake'}, 'fake-model')):
                result = negotiate(seller, buyer, use_llm=True)
                self.assertEqual(result, baseline)
                self.assertEqual(len(calls), 1)

    def test_request_quantity_and_supply_caps(self):
        for quantity in (0.25, 1000, 1000000):
            with self.subTest(quantity=quantity):
                result = run_pipeline(quantity_tonnes=quantity)
                self.assertLessEqual(result['quantity_tonnes'], min(quantity, 100000))
                self.assertGreater(result['quantity_tonnes'], 0)
                self.assertEqual(result['total_net_value_usd'], round(
                    result['margin_per_tonne_usd'] * result['quantity_tonnes'], 2))
        self.assertLessEqual(run_pipeline(seller_overrides={
            'available_quantity_tonnes_per_year': 15})['quantity_tonnes'], 15)

    def test_invalid_requests(self):
        cases = [{'quantity_tonnes': q} for q in (0, -1, True, '100', float('nan'), float('inf'))]
        cases += [{'seller_id': 'missing'}, {'objective': 'unsupported'}, {'material_id': ''},
                  {'buyer_overrides': {'missing': {'annual_demand_tonnes': 5}}},
                  {'buyer_overrides': {'shah_cement': {'buyer_id': 'changed'}}}]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                run_pipeline(**case)

    def test_total_value_winner(self):
        result = run_pipeline()
        self.assertEqual(result['buyer_id'], 'shah_cement')
        self.assertEqual(result['total_net_value_usd'], 1072500)

    def test_deadlines_and_same_port(self):
        with self.assertRaisesRegex(ValueError, 'deadline'):
            optimize_routes('dhamra', 'chittagong', 1000, 0.1)
        result = optimize_routes('dhamra', 'dhamra', 1000, 1)
        self.assertEqual(result['routes'][0]['transit_days'], 0)
        with self.assertRaises(ValueError):
            get_route_cost(result, 'missing')
        for invalid in (0, -1, float('nan'), True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                optimize_routes('dhamra', 'mongla', invalid, 20)

    def test_feasible_route_outside_cheapest_three(self):
        paths = [(['dhamra', 'chittagong'], 10000, cost) for cost in (1, 2, 3)]
        paths.append((['dhamra', 'chittagong'], 100, 4))
        with patch('agents.logistics._find_all_paths', return_value=paths):
            result = optimize_routes('dhamra', 'chittagong', 1000, 1)
        self.assertEqual(get_route_cost(result), 4)

    def test_numeric_validators_and_fractional_quantity(self):
        seller = {'min_acceptable_price_per_tonne_usd': 22,
                  'preferred_price_per_tonne_usd': 25, 'available_quantity_tonnes': 0.25}
        buyer = {'buyer_id': 'test', 'max_acceptable_price_per_tonne_usd': 28,
                 'annual_demand_tonnes': 100}
        self.assertEqual(negotiate(seller, buyer, use_llm=False)['quantity_tonnes'], 0.25)
        for invalid in (float('nan'), float('inf'), True, -1, '25'):
            with self.subTest(invalid=invalid):
                self.assertFalse(validate_proposal(invalid, 22, 28)[0])
                self.assertFalse(validate_proposal(25, invalid, 28)[0])
                self.assertFalse(validate_quantity(invalid, 100, 100)[0])
                with self.assertRaises(ValueError):
                    negotiate(seller, buyer, logistics_cost_per_tonne_usd=invalid, use_llm=False)
        self.assertEqual(negotiate(seller, buyer, logistics_cost_per_tonne_usd=100,
                                   use_llm=False)['status'], 'rejected')

    def test_missing_scenario_has_clean_error(self):
        result = subprocess.run([sys.executable, '-B', '-m', 'orchestrator.run',
                                 '--scenario', 'does-not-exist.json'], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('Traceback', result.stderr)

    def test_dataset_generator_reproduces_committed_snapshot(self):
        from data.generate_buyers import build_buyers
        self.assertEqual(build_buyers(), json.loads((ROOT / 'data/buyers.json').read_text()))


if __name__ == '__main__':
    unittest.main()
