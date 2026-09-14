#include <stdio.h>
#include <unistd.h>
#include <ftdi.h>
int main(int c,char**v){
  int iface = (c>1 && v[1][0]=='A') ? INTERFACE_A : INTERFACE_B;
  struct ftdi_context *f=ftdi_new(); ftdi_set_interface(f,iface);
  if(ftdi_usb_open(f,0x0403,0x6010)){printf("open: %s\n",ftdi_get_error_string(f));return 1;}
  ftdi_usb_reset(f); ftdi_set_latency_timer(f,2); ftdi_set_baudrate(f,9600);
  printf("iface %c sync bitbang=%d\n", iface==INTERFACE_A?'A':'B', ftdi_set_bitmode(f,0x07,BITMODE_SYNCBB));
  ftdi_tcioflush(f);
  unsigned char out[64], in[256];
  for(int i=0;i<64;i++) out[i] = (i/8)%2 ? 0x07 : 0x00;   /* 8 bytes low, 8 bytes high ... */
  int w=ftdi_write_data(f,out,64); usleep(300000);
  int tot=0,r; while((r=ftdi_read_data(f,in+tot,256-tot))>0) {tot+=r; usleep(50000);}
  printf("wrote %d, read back %d samples:\n", w, tot);
  for(int i=0;i<tot;i++) printf("%02x%s", in[i], (i%16==15)?"\n":" ");
  printf("\n"); ftdi_set_bitmode(f,0,BITMODE_RESET); ftdi_usb_close(f); return 0;}
