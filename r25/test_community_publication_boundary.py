"""Do not turn Community host installation into a bypass of Core/VMM proofs."""
from copy import deepcopy
import unittest
import core_business
import vmm_controller
import test_managed_vmm_publication as predecessors
from test_core_business import fixture_policy


class RetainedPublicationBoundary(unittest.TestCase):
    def test_current_publication_is_finite_and_new_core_needs_reviewed_successor(self):
        f=predecessors.Publication('runTest');f.setUp();self.addCleanup(f.doCleanups)
        f.prepare();self.assertEqual(f.f.install(),'INSTALLED');f.runtime()
        # The predecessor transaction fixture deliberately stubs its business
        # policy. Exercise candidate matching against the complete policy fixture.
        policy=fixture_policy(f.root)
        current=deepcopy(policy['upgrade']['candidate'])
        core_business.candidate_matches(policy,current)
        current['commit_sha']='9'*40
        with self.assertRaisesRegex(Exception,'business_candidate_not_approved'):
            core_business.candidate_matches(policy,current)
        with self.assertRaisesRegex(Exception,'publication_already_bound'):
            vmm_controller.capture_core_publication(f.rt,{})
        f.preserved()


if __name__=='__main__':unittest.main()
