from queue import PriorityQueue
from Node import node

# openList = pq
# no closed list
# Update graph as we traverse with nodes - simulate closed list

# Diagonal distance heuristic
# h(n)=c⋅max(∣n.x−goal.x∣,∣n.y−goal.y∣) c=cost of movement
# Euclidean distance heuristic
# h(n) ^2  ​​ =(n.x−goal.x) ​2 ​​ +(n.y−goal.y) ​^ 2​​

openNodes = PriorityQueue()

# while openNodes
# pop top off 
# iterate through successors
# if successors is out of bounds - skip
# if successor is 1 (terrain) - skip
# if successor is 0 - create new node, call node internals, push onto pq
# if successor is a node and closed - skip
# is successor is a node and on open - reevaluate
# set parent to closed 

# revaluate func
# compare g cost with new parent vs g cost with old parent
# if new g cost < old g cost, update parent,g, f cost
# what if new g = old g? 

