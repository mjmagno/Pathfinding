from queue import PriorityQueue
from Node import node

# Need and open list and a closed list
# Make open list a pq? Lowest f cost node on the open list is the node to search 

# From starting node calculate/update F cost of the adjacent nodes and evaluate lowest F cost node
# If node being evaluated is end point, return

# Need a "parent" value in potential node struct to indicate where they have come from
# Necessary so we can return the shortest path at the end of the algorithim
# Note - this arrow will not always point to the most recently evaluated node because of potential worse paths 

# Dealing with diagonals?

# What is the function taking as input? 
# File with a graph of 0s and 1s (0 is travelable, 1 is terrain)
# starting point and end point 

openNodes = PriorityQueue()


