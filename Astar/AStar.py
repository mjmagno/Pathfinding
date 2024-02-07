from queue import PriorityQueue
from Node import node

# From starting node calculate/update F cost of the adjacent nodes and evaluate lowest F cost node
# If node being evaluated is end point, return

# Need to think about overall structure more
# Node class is currently lacking x and y coordinates
# What would an evaluate function take in and return?
# Astar begins with starting node and evaluates adjacent nodes 
# Adjacent nodes are evaluated (calculate f cost) and placed onto open nodes list
# Top of open nodes is popped off and evaluated 
# Node structure needs x and y coordinates, as well as costs
# Where should costs be calculated? Helper function in node? Just update values in driver program?
# What structure should parent value be in Node?



# Distance algorithim?
#  h = sqrt ( (current_cell.x – goal.x)2 + 
#             (current_cell.y – goal.y)2 )
# League of Legends isn't like chess, you can move in any direction. Therefore, use Euclidean distance (?)
# https://stackoverflow.com/questions/46974075/a-star-algorithm-distance-heuristics



openNodes = PriorityQueue()


