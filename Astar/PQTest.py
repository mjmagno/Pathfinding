from queue import PriorityQueue
from Node import node

if __name__ == "__main__":
    n1 = node(1,2)
    n2 = node(3,3)
    n3 = node(10,15)
    n4 = node(6,7)
    n5 = node(-1,-3)
    pq = PriorityQueue()
    pq.put(n1)
    pq.put(n2)
    pq.put(n3)
    pq.put(n4)
    pq.put(n5)
    # Should print, -4, 3, 6, 13, 25
    while not pq.empty():
        print(pq.get().f)