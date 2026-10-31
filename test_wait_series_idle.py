import contextlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wait_series_idle as waiting


def response(session):
    return SimpleNamespace(returncode=0,stdout=json.dumps({'session':session}))

class WaitSeriesIdleTests(unittest.TestCase):
    def test_waits_for_focus_break_and_ready_without_mutating_anything(self):
        phases=[response({'phase':p}) for p in ('focus','break','ready')]+[response(None)]
        with patch.object(waiting.subprocess,'run',side_effect=phases) as rpc,patch.object(waiting.time,'sleep') as sleep,contextlib.redirect_stdout(io.StringIO()):
            waiting.wait_idle()
        self.assertEqual(sleep.call_count,3)
        self.assertEqual(rpc.call_count,4)
        for call in rpc.call_args_list:
            self.assertEqual(call.args[0],['/usr/bin/sudo','-n',waiting.HELPER,'study-status'])
    def test_already_idle_continues_without_wait(self):
        with patch.object(waiting.subprocess,'run',return_value=response(None)),patch.object(waiting.time,'sleep') as sleep:
            waiting.wait_idle()
        sleep.assert_not_called()
    def test_unknown_or_unavailable_status_stops_installation(self):
        for result in (SimpleNamespace(returncode=1,stdout=''),SimpleNamespace(returncode=0,stdout='{}'),response({'phase':'unexpected'})):
            with patch.object(waiting.subprocess,'run',return_value=result),patch.object(waiting.time,'sleep') as sleep:
                with self.assertRaises(RuntimeError):waiting.wait_idle()
                sleep.assert_not_called()

if __name__=='__main__':unittest.main()
