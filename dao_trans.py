# In order for this to work, you need to install parity (of version at least 1.3.0)
# and run it like this:
#
# parity --tracing on --pruning=archive
#
# Allow it to sync to at least the hard fork block before running this script
#

import httplib
import json
import sys

# This is part of output of command line:
# solc --hashes DAO.sol, where DAO.sol is the source code of theDAO
method_hashes_tex = '''
23b872dd: transferFrom(address,address,uint256)
4b6753bc: closingTime()
4e10c3ee: transferWithoutReward(address,uint256)
70a08231: balanceOf(address)
82661dc4: splitDAO(uint256,address)
a9059cbb: transfer(address,uint256)
be7c29c1: getNewDAOAddress(uint256)
dbde1988: transferFromWithoutReward(address,address,uint256)
'''

method_hashes = {}  # To look up the method signature based on the ABI
valid_hashes = set()  # To check if there is any method with this signature (to detect calls to fallback)
for line in method_hashes_tex.splitlines():
    parts = line.split(': ')
    if len(parts) > 1:
        method_hashes[parts[1]] = parts[0]
        valid_hashes.add(parts[0])

THE_DAO_ADDRESS = 'bb9bc244d798123fde783fcc1c72d3bb8c189413'
HARDFORK_BLOCK = 1920000


class Transfer:
    """ Represents one transfer of DAO tokens (via transfer function) """
    def __init__(self, transaction_hash_, source_address_, target_address_, tokens_):
        self.transaction_hash = transaction_hash_
        self.source_address = source_address_
        self.target_address = target_address_
        self.tokens = tokens_


def get_dao_creation_block(connection, dao_address):
    """ Uses binary search to find the block number at which the dao has been created """
    params = [{"to": "0x" + dao_address,  "data": "0x" + method_hashes['closingTime()']}]
    http.request(
        method='POST',
        url='',
        body=json.dumps({"jsonrpc": "2.0", "method": "eth_call", "params": params, "id": 0}),
        headers={'Content-Type': 'application/json'})
    response = http.getresponse()
    if response.status != httplib.OK:
        print 'Could not read childDAO closing time', response.status, response.reason
        sys.exit(0)
    closing_time = long(json.load(response)['result'][2:], 16)
    low_block_number = 1
    high_block_number = HARDFORK_BLOCK
    while high_block_number - low_block_number > 1:
        med_block_number = (low_block_number + high_block_number) / 2
        params = [str(med_block_number), "false"]
        http.request(
            method='POST',
            url='',
            body=json.dumps({"jsonrpc": "2.0", "method": "eth_getBlockByNumber", "params": params, "id": 0}),
            headers={'Content-Type': 'application/json'})
        response = http.getresponse()
        if response.status != httplib.OK:
            print 'Could not read block information', response.status, response.reason
            sys.exit(0)
        timestamp = long(json.load(response)['result']['timestamp'][2:], 16)
        if timestamp < closing_time:
            low_block_number = med_block_number
        else:
            high_block_number = med_block_number
    return low_block_number


# Open connection to parity JSON RPC
http = httplib.HTTPConnection('localhost:8545')
DAO_CREATION_BLOCK = get_dao_creation_block(http, THE_DAO_ADDRESS)


def traces_to_address(connection, to_address):
    """ Reads trace for an address from given HTTP connection """
    params = [{
        "fromBlock": str(DAO_CREATION_BLOCK),  # Block number where DAO was created
        "toBlock": str(HARDFORK_BLOCK),    # Block number of the hard-fork
    }]
    params[0]['toAddress'] = [to_address]
    connection.request(
    method='POST',
    url='',
    body=json.dumps({"jsonrpc": "2.0", "method": "trace_filter", "params": params, "id": 0}),
    headers={'Content-Type': 'application/json'})
    response = connection.getresponse()
    if response.status != httplib.OK:
        print 'Could not read to theDAO trace', response.status, response.reason
        sys.exit(0)
    print 'Parsing JSON...'
    return json.load(response)

# Get transactions sent to theDAO (internal or external)
print 'Reading transactions sent to theDAO via Parity JSON RPC...'
to_dao = traces_to_address(http, '0x' + THE_DAO_ADDRESS)

to_dao_result = to_dao['result']  # Array of all transactions to theDAO (internal and external)

transfer_list = []
all_addresses = set()  # Build up the list of all addresses involved in withdraws, splits, and transfers
proposal_id_by_address = {}
all_transactions = set()  # Build up the list of all transaction hashes, for re-tracing

for r in to_dao_result:
    result = r['result']
    if 'failedCall' in result:
        # Filter out failed transactions
        continue
    action = r['action']
    if 'call' not in action:
        # Filter out 'create' actions
        continue
    call = action['call']
    # Data sent with the transaction
    call_input = call['input']
    # First 2 characters are '0x', and we extract 20 bytes of address (40 hex digits)
    from_address = str(call['from'][2:2+40])
    transaction = r['transactionHash']
    # First 2 characters are '0x', then there are 8 hex digits (4 bytes) of method signature
    signature = str(call_input[2:2+8])
    if signature == method_hashes['transfer(address,uint256)'] or \
            signature == method_hashes['transferWithoutReward(address,uint256)']:
        output = long(result['call']['output'], 16)
        if output == 1:
            # First 2 characters are '0x', and we extract 20 bytes of address (40 hex digits)
            # First argument of transfer is the target_address (20 bytes, 40 hex digits),
            # pre-pended by 12 0-bytes (24 0 hex digits)
            target_address = str(call_input[34:74])
            # 32 bytes (64 hex digits) of the value
            tokens = long(call_input[74:138], 16)
            all_addresses.add(from_address)
            all_addresses.add(target_address)
            transfer_list.append(Transfer(transaction_hash_=transaction, source_address_=from_address, target_address_=target_address, tokens_=tokens))
            all_transactions.add(transaction)
    elif signature == method_hashes['transferFrom(address,address,uint256)'] or \
            signature == method_hashes['transferFromWithoutReward(address,address,uint256)']:
        output = long(result['call']['output'], 16)
        if output == 1:
            # First 2 characters are '0x', and we extract 20 bytes of address (40 hex digits)
            # First argument of transferFrom is the source_address (20 bytes, 40 hex digits),
            # pre-pended by 12 0-bytes (24 0 hex digits)
            source_address = str(call_input[34:74])
            # Second argument is the target_address (20 bytes, 40 hex digits),
            # pre-pended by 12 0-bytes (24 0 hex digits)
            target_address = str(call_input[98:138])
            # 32 bytes (64 hex digits) of the value
            tokens = long(call_input[138:138+64], 16)
            all_addresses.add(source_address)
            all_addresses.add(target_address)
            transfer_list.append(Transfer(transaction_hash_=transaction, source_address_=source_address, target_address_=target_address, tokens_=tokens))
            all_transactions.add(transaction)
    elif signature == method_hashes['splitDAO(uint256,address)']:
        # We only need to analyse splitDAO calls to
        # construct the map of addresses that might have some tokens in childDAOs
        proposal_id = int(call_input[10:10+64], 16)
        all_addresses.add(from_address)
        if from_address not in proposal_id_by_address:
            proposal_id_by_address[from_address] = set()
        proposal_id_by_address[from_address].add(proposal_id)


def retrace_transactions(connection, transaction):
    """ Reads traces for given list of transactions """
    connection.request(
    method='POST',
    url='',
    body='{"jsonrpc": "2.0", "method": "trace_transaction", "params": ["' + str(transaction) + '"], "id": 0}',
    headers={'Content-Type': 'application/json'})
    response = connection.getresponse()
    if response.status != httplib.OK:
        print 'Could not read traces of transactions', response.status, response.reason
        sys.exit(0)
    return json.load(response)

# Retrace all the transaction to see if they failed
# This needs to be done because sometimes the 'failedCall' is not present in all the internal
# transactions, even though the parent transaction failed
print 'Retracing all transactions via Parity JSON RPC...'
transactions_done = 0
failed_transactions = set()  # Store failed transaction here, and later use to filter out failed transfers
for transaction in all_transactions:
    retrace = retrace_transactions(http, transaction)
    for r in retrace['result']:
        if 'failedCall' in r['result']:
            failed_transactions.add(transaction)
    transactions_done += 1
    if transactions_done % 1000 == 0:
        print 'Retraced %d out of %d transaction' % (transactions_done, len(all_transactions))

print 'Failed transactions found during the retrace: %d' % len(failed_transactions)

# Filter out transfers in the failed transactions
transfer_list = [transfer for transfer in transfer_list if transfer.transaction_hash not in failed_transactions]

from itertools import groupby

# Aggregate transfers by source_address
transfer_by_source = {}
transfer_list.sort(key=lambda transfer: transfer.source_address)
for source_address, group in groupby(transfer_list, lambda transfer: transfer.source_address):
    total_transfer = sum(map(lambda transfer: transfer.tokens, group))
    transfer_by_source[source_address] = total_transfer

# Aggregate transfers by source_address
transfer_by_target = {}
transfer_list.sort(key=lambda transfer: transfer.target_address)
for target_address, group in groupby(transfer_list, lambda transfer: transfer.target_address):
    total_transfer = sum(map(lambda transfer: transfer.tokens, group))
    transfer_by_target[target_address] = total_transfer


def get_child_dao_address(connection, proposal_id):
    """ Reads address of childDAO given proposal_id """
    params = [{
        "to": "0x" + THE_DAO_ADDRESS,  # DAO address
        "data": "0x" + method_hashes['getNewDAOAddress(uint256)'] + format(proposal_id, '064x')
    }, "%d" % HARDFORK_BLOCK]
    http.request(
        method='POST',
        url='',
        body=json.dumps({"jsonrpc": "2.0", "method": "eth_call", "params": params, "id": 0}),
        headers={'Content-Type': 'application/json'})
    response = http.getresponse()
    if response.status != httplib.OK:
        print 'Could not read childDAO address', response.status, response.reason
        sys.exit(0)
    return json.load(response)['result'][26:]


child_dao_addresses = {}
child_dao_creation_blocks = {}
for proposal_id in range(1, 110):
    child_dao_address = str(get_child_dao_address(http, proposal_id))
    if child_dao_address != '' and child_dao_address != '0'*40:
        # Find block number at which the childDAO got created
        creation_block = get_dao_creation_block(http, child_dao_address)
        child_dao_addresses[proposal_id] = child_dao_address
        child_dao_creation_blocks[proposal_id] = creation_block


# For each address, request DAO token balance at the time of DAO creation
# and at the time of the hard fork
def read_dao_balance(connection, dao_address, holder_address, block_number):
    """ Reads DAO balance for an address at given block from given HTTP connection """
    params = [{
        "to": "0x" + dao_address,  # DAO address
        "data": "0x" + method_hashes['balanceOf(address)'] + '0'*24 + holder_address
    }]
    connection.request(
        method='POST',
        url='',
        body=json.dumps({"jsonrpc": "2.0", "method": "eth_call", "params": params + [str(block_number)], "id": 0}),
        headers={'Content-Type': 'application/json'})
    response = connection.getresponse()
    if response.status != httplib.OK:
        print 'Could not read to theDAO balance', response.status, response.reason
        sys.exit(0)
    return int(json.load(response)['result'][2:], 16)


class AddressInfo:
    """ Stores information about one address to be grouped by proposal_id and printed """
    def __init__(self, address_, tokens_burnt_, child_tokens_):
        self.address = address_
        self.tokens_burnt = tokens_burnt_
        self.child_tokens = child_tokens_

address_infos_by_proposal = {}
addresses_done = 0
for a in all_addresses:
    balance_at_creation = read_dao_balance(http, THE_DAO_ADDRESS, a, DAO_CREATION_BLOCK)
    balance_at_hardfork = read_dao_balance(http, THE_DAO_ADDRESS, a, HARDFORK_BLOCK)
    transferred_from_a = transfer_by_source[a] if a in transfer_by_source else 0
    transferred_to_a = transfer_by_target[a] if a in transfer_by_target else 0
    tokens_burnt = balance_at_creation + transferred_to_a - transferred_from_a - balance_at_hardfork
    child_tokens = {}
    total_child_tokens = 0
    if a in proposal_id_by_address:
        for proposal_id in proposal_id_by_address[a]:
            child_dao_address = child_dao_addresses[proposal_id]
            child_dao_creation_block = child_dao_creation_blocks[proposal_id]
            child_dao_balance_at_hardfork = read_dao_balance(http, child_dao_address, a, child_dao_creation_block)
            if child_dao_balance_at_hardfork > 0:
                child_tokens[proposal_id] = child_dao_balance_at_hardfork
                total_child_tokens += child_dao_balance_at_hardfork
    if a != '0'*40 and (len(child_tokens) > 0 or tokens_burnt != 0):
        for proposal_id in child_tokens:
            if proposal_id not in address_infos_by_proposal:
                address_infos_by_proposal[proposal_id] = []
            address_infos_by_proposal[proposal_id].append(AddressInfo(address_=a, tokens_burnt_=tokens_burnt, child_tokens_=child_tokens))
    addresses_done += 1
    if addresses_done % 1000 == 0:
        print 'Checked %d out of %d addresses' % (addresses_done, len(all_addresses))

for proposal_id in sorted(address_infos_by_proposal.keys()):
    print '================================================='
    print 'Proposal #%d' % proposal_id
    print '-------------------------------------------------'
    for address_info in address_infos_by_proposal[proposal_id]:
        total_child_tokens = sum((tokens for p, tokens in address_info.child_tokens.iteritems() if p == proposal_id))
        print address_info.address, 'burnt:', address_info.tokens_burnt, 'childTokens:', address_info.child_tokens, \
            'ratio', 'inf' if address_info.tokens_burnt == 0 else float(total_child_tokens) / float(address_info.tokens_burnt)
    print

http.close()
