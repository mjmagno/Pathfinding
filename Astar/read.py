# Takes in a file name and outputs a graph containing file contents
# File must only contain 1s and 0s
def read(fileName):
    text_file = open(fileName,'r')
    g = []
    for line in text_file:
        r = []
        for c in line:
            if (c == '1' or c == '0') :
                r.append(int(c))
        g.append(r)
    text_file.close()
    return g
