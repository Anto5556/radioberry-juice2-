#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <ftdi.h>
static struct ftdi_context *f;
static int pins(const char*t){unsigned char p=0; ftdi_read_pins(f,&p); printf("  %-28s pins=0x%02x nSTATUS=%d CONF_DONE=%d\n",t,p,!!(p&8),!!(p&16)); return p;}
int main(int argc,char**argv){
  FILE*fp=fopen(argv[1],"rb"); if(!fp){perror("rbf");return 1;}
  fseek(fp,0,SEEK_END); long n=ftell(fp); rewind(fp); unsigned char*img=malloc(n); fread(img,1,n,fp); fclose(fp);
  f=ftdi_new(); ftdi_set_interface(f,INTERFACE_B);
  if(ftdi_usb_open(f,0x0403,0x6010)){printf("open: %s\n",ftdi_get_error_string(f));return 1;}
  ftdi_set_latency_timer(f,1); ftdi_set_baudrate(f,1200000/4);
  ftdi_set_bitmode(f,0x07,BITMODE_BITBANG); ftdi_write_data_set_chunksize(f,4096);
  pins("before");
  unsigned char b=0x00; ftdi_write_data(f,&b,1); usleep(100000); pins("nCONFIG low");
  b=0x04; ftdi_write_data(f,&b,1); usleep(200000); pins("nCONFIG high");
  unsigned char*buf=malloc(n*24+16); long k=0;
  for(long i=0;i<n;i++){unsigned char byte=img[i]; for(int j=0;j<8;j++){unsigned char d=0x04|((byte&1)<<1); buf[k++]=d; buf[k++]=d|1; buf[k++]=d; byte>>=1;}}
  for(int i=0;i<8;i++){buf[k++]=0x05; buf[k++]=0x04;}
  long off=0; while(off<k){int c=k-off>60000?60000:k-off; int w=ftdi_write_data(f,buf+off,c); if(w<0){printf("write err %s\n",ftdi_get_error_string(f));break;} off+=w;}
  usleep(200000); int p=pins("after upload + clocks");
  printf("RESULT %s: %s\n",argv[1],(p&16)?"CONFIGURED (CONF_DONE=1)":"FAILED (CONF_DONE=0)");
  ftdi_set_bitmode(f,0,BITMODE_RESET); ftdi_usb_close(f); return 0;}
