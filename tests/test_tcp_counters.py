"""質問者の TCP の再送の計数（cpu_stat の read/parse/diff）の仕様：/proc/net/snmp・netstat の書式から 3 つの計数を読み，差を取る．"""

from __future__ import annotations

from atrium.cpu_stat import diff_tcp_counters, parse_proc_net_counters

SNMP = "Ip: Forwarding InReceives\nIp: 1 10\nTcp: RtoAlgorithm RetransSegs InErrs\nTcp: 1 7 2\n"
NETSTAT = "TcpExt: SyncookiesSent TCPSynRetrans TCPTimeouts\nTcpExt: 0 3 5\n"


def test_parse_proc_net_counters_pairs_names_and_values_by_prefix() -> None:
    parsed = parse_proc_net_counters(SNMP + NETSTAT)
    assert parsed["Tcp"]["RetransSegs"] == 7
    assert parsed["TcpExt"] == {"SyncookiesSent": 0, "TCPSynRetrans": 3, "TCPTimeouts": 5}


def test_diff_tcp_counters_subtracts_each_counter() -> None:
    before = {"RetransSegs": 7, "TCPSynRetrans": 3, "TCPTimeouts": 5}
    after = {"RetransSegs": 9, "TCPSynRetrans": 3, "TCPTimeouts": 6}
    assert diff_tcp_counters(before, after) == {
        "RetransSegs": 2,
        "TCPSynRetrans": 0,
        "TCPTimeouts": 1,
    }


def test_diff_tcp_counters_is_none_when_either_sample_is_unreadable() -> None:
    assert diff_tcp_counters(None, {"RetransSegs": 1}) is None
    assert diff_tcp_counters({"RetransSegs": 1}, None) is None
