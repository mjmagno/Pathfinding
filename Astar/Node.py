# G cost - Cost of the path from the start node to n
# H cost - Estimate of cost from this node to end 
# F cost - G cost + H cost
class Node:
    endNode = None

    def calculateH(self):
        selfX = self.coords[0]
        selfY = self.coords[1]
        endX = self.endNode[0]
        endY = self.endNode[1]

        # Diagonal distance heuristic
        # h(n)=c⋅max(∣n.x−goal.x∣,∣n.y−goal.y∣) c=cost of movement
        # c = 1 for this algorithim
        # distance to move diagonally and non diagonal are the same
        return max(abs(selfX - endX),abs(selfY - endY))
    
    def __init__(self, coords, parent, end = None):
        self.coords = coords
        self.parent = parent
        # Start node case
        if not (parent):
             self.g = 0
             Node.endNode = end
        else:
            self.g = parent.g + 1
        


        self.h = self.calculateH()
        self.f = self.g + self.h
        self.listStatus = "open"
     
    # Custom comparators compare f cost, necessary because of the use of priority queues 
    def __lt__(self, obj):
        """self < obj."""
        # If node f costs are tied, compare h costs instead
        if (self.f == obj.f):
            return (self.h) < (obj.h)
        return (self.f) < (obj.f)

    # returns a list of python tuples (points) based on this node's coordinates
    def getSuccessors(self): 
        # 8 directional neighbors 
        neighbors = [(0,1),(1,0),(0,-1),(-1,0),(1,1),(-1,1),(1,-1),(-1,-1)]
        successors = []
        for neighbor in neighbors:
            successor = neighbor[0] + self.coords[0], neighbor[1] + self.coords[1]
            successors.append(successor)
        return successors

    # takes in a different parent node and checks if a better route is available through a different parent
    # updates node, returns bool 
    def reevaluate(self,alternateParent):
            if (alternateParent.g + 1 < self.g):
                 self.g = alternateParent.g + 1
                # self.parent vs parent 
                 self.parent = alternateParent
                 return True
            return False
    
    # backtracks though graph to return the shortest path between the start and end point
    # returns the list of coordinates corresponding to this path
    def returnPath(self):
        node = self
        path = []
        while(node):
             path.append(node.coords)
             node = node.parent
        return path