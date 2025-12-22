#!/bin/sh
# Configure IPFS for private/offline mode
# This script runs after ipfs init but before the daemon starts
# See: https://github.com/ipfs/kubo/blob/master/docs/environment-variables.md

set -e

echo "=== Configuring IPFS for private mode ==="

# Disable DHT routing (we don't need to participate in the public network)
ipfs config Routing.Type none

# Remove all bootstrap nodes (prevents auto-connecting to public network)
ipfs bootstrap rm --all

# Clear address announcements (we don't want to advertise our address)
ipfs config --json Addresses.Announce '[]'
ipfs config --json Addresses.NoAnnounce '["/ip4/0.0.0.0/ipcidr/0", "/ip6/::/ipcidr/0"]'

# Set conservative connection manager limits (in case any peers do connect)
ipfs config --json Swarm.ConnMgr.LowWater 0
ipfs config --json Swarm.ConnMgr.HighWater 10
ipfs config --json Swarm.ConnMgr.GracePeriod '"1m"'

# Disable relay services (we don't need to relay for others)
ipfs config --json Swarm.RelayClient.Enabled false
ipfs config --json Swarm.RelayService.Enabled false

# Disable hole punching (not needed for private node)
ipfs config --json Swarm.EnableHolePunching false

# Disable AutoNAT (we don't need NAT traversal detection)
ipfs config --json AutoNAT.ServiceMode '"disabled"'

# Disable resource manager announce (reduces overhead)
ipfs config --json Swarm.ResourceMgr.Enabled false

echo "=== Private mode configuration complete ==="
echo "Routing.Type: $(ipfs config Routing.Type)"
echo "Bootstrap nodes: $(ipfs bootstrap list | wc -l)"
