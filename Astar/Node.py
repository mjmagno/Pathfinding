# G cost - Distance from starting node to this node
# H cost - Distance from end node to this node 
# F cost - G cost + H cost
# How are we calculating costs? 

# Custom comparators compare f cost, necessary because of the use of priority queues 
class node:
    def __init__(self, hCost, gCost, parent = None):
        self.h = hCost
        self.g = gCost
        self.f = hCost + gCost
        self.parent = parent
    
    #TODO What to do if f costs are tied 
    def __lt__(self, obj):
        """self < obj."""
        # If node f costs are tied, compare h costs instead
        if (self.f == obj.f):
            return (self.h) < (obj.h)
        return (self.f) < (obj.f)

    # def __le__(self, obj):
    #     """self <= obj."""
    #     return (self.f) <= (obj.f)

    # def __eq__(self, obj):
    #     """self == obj."""
    #     return (self.f) == (obj.f)

    # def __ne__(self, obj):
    #     """self != obj."""
    #     return (self.f) != (obj.f)

    # def __gt__(self, obj):
    #     """self > obj."""
    #     return (self.f) > (obj.f)

    # def __ge__(self, obj):
    #     """self >= obj."""
    #     return (self.f) >= (obj.f)
