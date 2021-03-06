#!/usr/bin/env python3
# Copyright (c) 2015-2018 The Bitcoin Unlimited developers
# Copyright (c) 2020 The Ion Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.

import time
import sys
import copy
if sys.version_info[0] < 3:
    raise "Use Python 3"
import logging

from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import *
import test_framework.ionlib as ionlib
from test_framework.nodemessages import *
from test_framework.script import *

class MyTest (BitcoinTestFramework):

    def setup_chain(self, bitcoinConfDict=None, wallets=None):
        print("Initializing test directory " + self.options.tmpdir)
        initialize_chain(self.options.tmpdir, bitcoinConfDict, wallets)

    def setup_network(self, split=False):
        self.nodes = start_nodes(2, self.options.tmpdir)
        connect_nodes_bi(self.nodes, 0, 1)
        self.is_network_split = False
        self.sync_all()

    def runScriptMachineTests(self):

        # Check basic script
        sm = ionlib.ScriptMachine()
        worked = sm.eval(CScript([OP_1, OP_0, OP_5, OP_6, OP_TOALTSTACK, OP_TOALTSTACK]))
        assert(worked)
        # Check stack
        stk = sm.stack()
        assert_equal(int.from_bytes(stk[1], byteorder='little'), 1)
        assert_equal(len(stk[0]), 0)

        altstk = sm.altstack()
        assert_equal(int.from_bytes(altstk[0], byteorder='little'), 5)
        assert_equal(int.from_bytes(altstk[1], byteorder='little'), 6)

        worked = sm.eval(CScript([OP_FROMALTSTACK, OP_FROMALTSTACK]))
        assert(worked)
        stk = sm.stack()
        assert_equal(int.from_bytes(stk[0], byteorder='little'), 6)

        # Check reset
        sm.reset()
        stk = sm.stack()
        assert(len(stk) == 0)
        altstk = sm.altstack()
        assert(len(altstk) == 0)

        # Check stepping
        worked = sm.eval(CScript([OP_1, OP_1]))
        assert(worked)
        worked = sm.begin(CScript([OP_IF, OP_IF, OP_2, OP_ELSE, OP_3, OP_ENDIF, OP_ENDIF]))
        count = 0
        try:
            while 1:
                count += 1
                sm.step()
                assert(sm.pos() == count)  # only matches count because every opcode in the script is 1 byte (no pushdata)
        except ionlib.Error as e:
            assert(str(e) == 'stepped beyond end of script')
        stk = sm.stack()
        assert_equal(int.from_bytes(stk[0], byteorder='little'), 2)

        sm.reset()
        # Check clone
        worked = sm.eval(CScript([OP_1, OP_1]))
        assert(worked)
        sm2 = sm.clone()
        worked = sm.eval(CScript([OP_IF, OP_IF, OP_2, OP_ELSE, OP_3, OP_ENDIF, OP_ENDIF]))
        stk = sm.stack()
        assert_equal(int.from_bytes(stk[0], byteorder='little'), 2)
        sm.cleanup()

        worked = sm2.eval(CScript([OP_IF, OP_IF, OP_3, OP_ELSE, OP_2, OP_ENDIF, OP_ENDIF]))
        assert(worked)
        stk = sm2.stack()
        assert_equal(int.from_bytes(stk[0], byteorder='little'), 3)
        sm2.cleanup()

        # Check stack assignment
        sm = ionlib.ScriptMachine()
        worked = sm.eval(CScript([OP_1, OP_1]))
        assert(worked)
        sm.setStackItem(1, b"")
        worked = sm.eval(CScript([OP_IF, OP_IF, OP_2, OP_ELSE, OP_3, OP_ENDIF, OP_ENDIF]))
        assert(worked)
        stk = sm.stack()
        # since I overwrote a true with a false, the else condition should have been taken
        assert_equal(int.from_bytes(stk[0], byteorder='little'), 3)

        # Check stack push
        sm.reset()
        sm.setStackItem(-1, b"")
        sm.setStackItem(-1, bytes([1]))
        worked = sm.eval(CScript([OP_IF, OP_IF, OP_2, OP_ELSE, OP_3, OP_ENDIF, OP_ENDIF]))
        assert(worked)
        stk = sm.stack()
        assert_equal(int.from_bytes(stk[0], byteorder='little'), 3)
        assert(sm.error()[0] == 0)

        # Check script error
        sm.reset()
        sm.setStackItem(-1, bytes([1]))
        worked = sm.eval(CScript([OP_IF, OP_IF, OP_2, OP_ELSE, OP_3, OP_ENDIF, OP_ENDIF]))
        assert(not worked)
        err = sm.error()
        assert_equal(err[0], ionlib.ScriptError.SCRIPT_ERR_UNBALANCED_CONDITIONAL)
        assert_equal(err[1], 2)

    def run_test(self):
        self.runScriptMachineTests()

        faulted = False
        try:
            ionlib.spendscript(OP_1)
        except AssertionError:
            faulted = True
            pass
        assert faulted, "only data in spend scripts"

        try:
            ionlib.signTxInput(b"", 0, 5, b"", b"", ionlib.SIGHASH_ALL)
        except AssertionError:
            faulted = True
            pass
        assert faulted, "not signing with ion core forkid"

        # grab inputs from 2 different full nodes and sign a single tx that spends them both
        wallets = [self.nodes[0].listunspent(), self.nodes[1].listunspent()]
        inputs = [x[0] for x in wallets]
        privb58 = [self.nodes[0].dumpprivkey(inputs[0]["address"]), self.nodes[1].dumpprivkey(inputs[1]["address"])]

        privkeys = [decodeBase58(x)[1:-5] for x in privb58]
        pubkeys = [ionlib.pubkey(x) for x in privkeys]

        tx = CTransaction()
        for i in inputs:
            tx.vin.append(CTxIn(COutPoint(i["txid"], i["vout"]), b"", 0xffffffff))

        destPrivKey = ionlib.randombytes(32)
        destPubKey = ionlib.pubkey(destPrivKey)
        destHash = ionlib.addrbin(destPubKey)

        output = CScript([OP_DUP, OP_HASH160, destHash, OP_EQUALVERIFY, OP_CHECKSIG])

        amt = int(sum([x["amount"] for x in inputs]) * ionlib.BCH)
        tx.vout.append(CTxOut(amt, output))

        sighashtype = 0x41
        n = 0
        for i, priv in zip(inputs, privkeys):
            sig = ionlib.signTxInput(tx, n, i["amount"], i["scriptPubKey"], priv, sighashtype)
            tx.vin[n].scriptSig = ionlib.spendscript(sig)  # P2PK
            n += 1

        txhex = hexlify(tx.serialize()).decode("utf-8")
        txid = self.nodes[0].enqueuerawtransaction(txhex)

        assert txid == hexlify(ionlib.txid(txhex)[::-1]).decode("utf-8")

        # Now spend the created output to an anyone can spend address
        tx2 = CTransaction()
        tx2.vin.append(CTxIn(COutPoint(ionlib.txid(txhex), 0), b"", 0xffffffff))
        tx2.vout.append(CTxOut(amt, CScript([OP_1])))
        sig2 = ionlib.signTxInput(tx2, 0, amt, output, destPrivKey, sighashtype)
        tx2.vin[0].scriptSig = ionlib.spendscript(sig2, destPubKey)

        # Local script interpreter:
        # Check that the spend works in a transaction-aware script machine
        txbad = copy.deepcopy(tx2)
        badsig = list(sig2)
        badsig[10] = 1  # mess up the sig
        badsig[11] = 2
        txbad.vin[0].scriptSig = ionlib.spendscript(bytes(badsig), destPubKey)

        # try a bad script (sig check should fail)
        sm = ionlib.ScriptMachine(tx=tx2, inputIdx=0, inputAmount=tx.vout[0].nValue)
        ret = sm.eval(txbad.vin[0].scriptSig)
        assert(ret)
        ret = sm.eval(tx.vout[0].scriptPubKey)
        assert(not ret)
        assert(sm.error()[0] == ionlib.ScriptError.SCRIPT_ERR_SIG_NULLFAIL)

        # try a good spend script
        sm.reset()
        ret = sm.eval(tx2.vin[0].scriptSig)
        assert(ret)
        ret = sm.eval(tx.vout[0].scriptPubKey)
        assert(ret)

        # commit the created transaction
        tx2id = self.nodes[0].enqueuerawtransaction(hexlify(tx2.serialize()).decode("utf-8"))

        # Check that all tx were created, and commit them
        waitFor(20, lambda: self.nodes[0].getmempoolinfo()["size"] == 2)
        blk = self.nodes[0].generate(1)
        self.sync_blocks()
        assert self.nodes[0].getmempoolinfo()["size"] == 0
        assert self.nodes[1].getmempoolinfo()["size"] == 0


if __name__ == '__main__':
    env = os.getenv("BITCOIND", None)
    if env is None:
        env = os.path.dirname(os.path.abspath(__file__))
        env = env + os.sep + ".." + os.sep + ".." + os.sep + "src" + os.sep + "bitcoind"
        env = os.path.abspath(env)
    path = os.path.dirname(env)
    try:
        ionlib.init(path + os.sep + ".libs" + os.sep + "libioncore.so")
        MyTest().main()
    except OSError as e:
        print("Issue loading shared library.  This is expected during cross compilation since the native python will not load the .so: %s" % str(e))

# Create a convenient function for an interactive python debugging session


def Test():
    t = MyTest()
    bitcoinConf = {
        "debug": ["rpc", "net", "blk", "thin", "mempool", "req", "bench", "evict"],
    }

    flags = [] # ["--nocleanup", "--noshutdown"]
    if os.path.isdir("/ramdisk/test"):
        flags.append("--tmpdir=/ramdisk/test/ionlibtest")
    binpath = findBitcoind()
    flags.append("--srcdir=%s" % binpath)
    ionlib.init(binpath + os.sep + ".libs" + os.sep + "libioncore.so")
    t.main(flags, bitcoinConf, None)
