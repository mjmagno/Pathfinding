import heapq
from Node import Node
from read import read

# Takes in a file name that contains a m x n graph of 0s and 1s, a start point, and an end point
# Outputs the shortest path between the two points
def aStar(file_name,start,end):
    g = read(file_name)
    m = len(g)
    n = len(g[0])
    openNodes = []
    startNode = Node(start,None,end)
    g[start[1]][start[0]] = startNode
    heapq.heappush(openNodes,startNode)
    # while nodes are on the open list
    while (openNodes):
        parent = heapq.heappop(openNodes)
        for successor in parent.getSuccessors(): 
            x = successor[0]
            y = successor[1]
             # path to end node has been found 
            if (successor == end):
                #TODO band aid fix because successor isn't a node - don't want to create a node for the end
                #For some reason end point is being included on the path, this is a good thing but it shouldn't be due to the band aid fix
                return parent.returnPath()
            # if sucessor is out of bounds, skip
            elif (x >= n or y >= m or x < 0 or y < 0 ):
                continue
            # if sucessor is a piece of terrain, skip
            elif (g[y][x] == 1):
                continue
            # if sucessor is 0, create a new node and push onto open list
            elif (g[y][x] == 0):
                node = Node(successor,parent)
                heapq.heappush(openNodes,node)
                g[y][x] = node
            # sucessor is a node
            else:
                # if on the closed list, skip
                if (g[y][x].listStatus == "closed"):
                    continue
                # else, reevaluate
                # if route through this parent node is better, update pq 
                if(g[y][x].reevaluate(parent)):
                    openNodes.remove(g[y][x])   
                    heapq.heappush(openNodes, g[x][y])

        parent.listStatus = "closed"

#TODO test reevaulate function (when alternate path is found, when one parent is as good as another)
#TODO test different heuristics (euclidean distance) and their performances
#TODO test load (extremely large graphs)
