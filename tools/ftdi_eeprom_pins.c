#include <stdio.h>
#include <unistd.h>
#include <ftdi.h>
int main(){
  struct ftdi_context *f=ftdi_new();
  ftdi_set_interface(f,INTERFACE_B);
  if(ftdi_usb_open(f,0x0403,0x6010)){printf("open: %s\n",ftdi_get_error_string(f));return 1;}
  if(ftdi_read_eeprom(f)==0 && ftdi_eeprom_decode(f,0)==0){
    int a,b; ftdi_get_eeprom_value(f,CHANNEL_A_TYPE,&a); ftdi_get_eeprom_value(f,CHANNEL_B_TYPE,&b);
    int ad,bd; ftdi_get_eeprom_value(f,CHANNEL_A_DRIVER,&ad); ftdi_get_eeprom_value(f,CHANNEL_B_DRIVER,&bd);
    printf("EEPROM chanA type=%d drv=%d  chanB type=%d drv=%d  (0=UART 1=FIFO 2=OPTO 4=CPU 8=FT1284)\n",a,ad,b,bd);
  } else printf("eeprom read: %s\n",ftdi_get_error_string(f));
  ftdi_set_baudrate(f,9600);
  printf("bitbang: %d\n",ftdi_set_bitmode(f,0x07,BITMODE_BITBANG));
  unsigned char seq[]={0x04,0x05,0x04,0x06,0x04,0x00,0x04};
  for(int i=0;i<7;i++){unsigned char p=0; int w=ftdi_write_data(f,&seq[i],1); usleep(150000); ftdi_read_pins(f,&p);
    printf("write 0x%02x (w=%d) -> pins 0x%02x  DCLK=%d DATA0=%d nCONFIG=%d nSTATUS=%d CONF_DONE=%d\n",seq[i],w,p,p&1,!!(p&2),!!(p&4),!!(p&8),!!(p&16));}
  ftdi_disable_bitbang(f); ftdi_usb_close(f); ftdi_free(f); return 0;}
