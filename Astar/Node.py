# G cost - Cost of the path from the start node to n
# H cost - Estimate of cost from this node to end 
# F cost - G cost + H cost
# How are we calculating costs? 

# Custom comparators compare f cost, necessary because of the use of priority queues 

# Static variable of end point
# Calculate h, g, f cost functions
# Reevaluate function - takes in a different parent 
class node:
    def __init__(self, xCoord, yCoord, parent = None):
        self.x = xCoord
        self.y = yCoord
        # calculate g,h,f
        self.listStatus = "open"
     

    def __lt__(self, obj):
        """self < obj."""
        # If node f costs are tied, compare h costs instead
        if (self.f == obj.f):
            return (self.h) < (obj.h)
        return (self.f) < (obj.f)
