#include <stdio.h>
#include <unistd.h>
#include "ftd2xx.h"
static void show(FT_HANDLE h,const char*t){UCHAR p=0;FT_GetBitMode(h,&p);DWORD q=0;FT_GetQueueStatus(h,&q);
 printf("%-26s pins=0x%02x nSTATUS=%d CONF_DONE=%d rxq=%u\n",t,p,!!(p&8),!!(p&16),q);}
int main(int c,char**v){
  const char*name=c>1?v[1]:"radioberry-juice B";
  DWORD n=0; FT_CreateDeviceInfoList(&n); FT_DEVICE_LIST_INFO_NODE info[8]; FT_GetDeviceInfoList(info,&n);
  for(DWORD i=0;i<n;i++) printf("dev%u type=%u id=%08x flags=%x serial=%s desc='%s'\n",i,info[i].Type,info[i].ID,info[i].Flags,info[i].SerialNumber,info[i].Description);
  FT_HANDLE h; FT_STATUS s=FT_OpenEx((PVOID)name,FT_OPEN_BY_DESCRIPTION,&h); printf("open '%s' = %d\n",name,(int)s); if(s)return 1;
  printf("reset=%d\n",(int)FT_ResetDevice(h));
  printf("bm0=%d\n",(int)FT_SetBitMode(h,0,0)); usleep(100000);
  printf("bm async=%d\n",(int)FT_SetBitMode(h,0x07,FT_BITMODE_ASYNC_BITBANG));
  FT_SetBaudRate(h,9600); FT_SetTimeouts(h,500,500); FT_Purge(h,3);
  unsigned char seq[]={0x04,0x00,0x04,0x02,0x06,0x07,0x01};
  for(int i=0;i<7;i++){DWORD w=0; s=FT_Write(h,&seq[i],1,&w); usleep(100000); char t[40]; sprintf(t,"wrote 0x%02x st=%d w=%u",seq[i],(int)s,w); show(h,t);}
  unsigned char buf[256]; DWORD r=0; FT_GetQueueStatus(h,&r); if(r){FT_Read(h,buf,r>256?256:r,&r); printf("rx %u bytes, last 0x%02x\n",r,buf[r-1]);}
  FT_SetBitMode(h,0,0); FT_Close(h); return 0; }
