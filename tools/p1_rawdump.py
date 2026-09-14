import socket,struct,time
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(("0.0.0.0",0)); s.settimeout(1)
def ep2(seq,c0,c1,c2,c3,c4):
    sub=bytes([0x7F,0x7F,0x7F,c0,c1,c2,c3,c4])+bytes(504)
    return b"\xef\xfe\x01\x02"+struct.pack(">I",seq)+sub+sub
s.sendto(ep2(0,0,0,0,0,4),("127.0.0.1",1024))
s.sendto(b"\xef\xfe\x04\x01"+bytes(60),("127.0.0.1",1024))
fr=[]
while len(fr)<400:
    try: d,_=s.recvfrom(2048)
    except socket.timeout: break
    fr.append(d)
s.sendto(b"\xef\xfe\x04\x00"+bytes(60),("127.0.0.1",1024))
print("seqs:",[struct.unpack(">I",d[4:8])[0] for d in fr[:12]], "...", [struct.unpack(">I",d[4:8])[0] for d in fr[-5:]])
d=fr[200]
for off in range(0,1032,24):
    print("%4d "%off+d[off:off+24].hex(" "))
