#!/usr/bin/env python
from struct import *
import binascii
import time
import socket
import logging
import datetime
from sys import argv

'''
Header          BINARY_STREAM[4]    s
Operation Code  INT16               h
Reserved        INT8                b
Reserved        BINARY_STREAM[8]    s
Text length     INT32               i
Reserved        INT32
Text            BINARY_STREAM[n]    s
'''

class Camera:
  STRUCTURE = '<4s h b 8s I I'
  UNPACK = '<4s h b 8s I I'
  BUFFER_SIZE = 2048
  
  UNPACK_LENGTH = 6
  
  def __init__(self, ip, port):
    self.logger = logging.getLogger('Camera')
    self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.s.connect((ip, port))
    
    self._ip = ip
    self._port = port

  def __del__(self):
    self.s.close()
  
  def login(self, user_name, password):
    self._login_request()
    data = self.s.recv(Camera.BUFFER_SIZE)
    data = self._handle_login_response(data)
    if data[6] == 0:
      self.s.send(self._verify_request(user_name, password))
      data = self.s.recv(Camera.BUFFER_SIZE)
      data = self._handle_verify_response(data)
      if data[6] == 0:
        self.s.send(self._talk_start_request())
        return self.handle()
      elif data[6] == 1:
         raise Exception('User error')
      elif data[6] == 5:
         raise Exception('Pwd error')
    else:
      raise Exception('Too many open connections!?')
    
  def handle(self):
    while True:
      binary_string = self.s.recv(Camera.BUFFER_SIZE)
      data = unpack_from(Camera.UNPACK, binary_string)
      if data[1] == 12:
        return self._handle_talk_start_response(binary_string)
      else:
        print 'Got wrong msg', data[0], data[1]
  
  def _login_request(self):
    '''
      Operation Code: 0
    '''
    data = pack(Camera.STRUCTURE, 'MO_O', 0, 0, '', 0, 0)
    self.s.send(data)
    
  def _handle_login_response(self, binary_string):
    '''
      Operation Code: 1
    Result: INT16                               (0: ok, 2: too many connections)
    Camera ID: BINARY_STREAM[13]                (Only exists when result == 0)
    Reserved: BINARY_STREAM[4]                  (Only exists when result == 0)
    Reserved: BINARY_STREAM[4]                  (Only exists when result == 0)
    Camera firmware version: BINARY_STREAM[4]   (Only exists when result == 0)
    '''
    format = Camera.UNPACK + 'h 13s 4s 4s 4s'
    data =  self._unpack_data(format, binary_string)
    return data
      
  def _verify_request(self, user, password):
    '''
      Operation Code: 2
    User: BINARY_STREAM[13]
    Password: BINARY_STREAM[13]
    '''
    data = pack('<4s h b 8s I I 13s 13s', 'MO_O', 2, 0, '', 26, 26, user, password)
    return data
    
  def _handle_verify_response(self, binary_string):
    '''
      Operation Code: 3
    result: INT16   (0: correct, 1: user error, 5:pwd error)
    Reserved: INT8  (Only exists when result == 0)
    '''
    format = Camera.UNPACK + 'h b'
    data = self._unpack_data(format, binary_string)
    return data
    
  def _talk_start_request(self):
    '''
      Operation Code: 11
    Camera audio playback buffer(seconds): INT8 (>= 1)
    '''
    data = pack('<4s h b 8s I I b', 'MO_O', 11, 0, '', 1, 1, 1)
    return data

  def _handle_talk_start_response(self, binary_string):
    '''
      Operation Code: 12
    result: INT16              (0: agree, 2: too many connections)
    Data connection ID: INT32  (Only exists when result == 0)
    '''
    format = Camera.UNPACK + 'h I'
    data = self._unpack_data(format, binary_string)
    return data[Camera.UNPACK_LENGTH]==0, data[Camera.UNPACK_LENGTH+1]    
    
  def _login_request_data(self, dataConnectionId):
    '''
      Header: MO_V
      Operation Code: 0
    Data connection ID: INT32 (connection drops when id incorrect)
    '''
    data = pack('<4s h b 8s I I I', 'MO_V', 0, 0, '', 4, 4, dataConnectionId)
    return data

  def create_talk_data(self, serial, adpcm_data):
    '''
      Header: MO_V
      Operation Code: 3
    Timestamp (1ms): INT32            (Can use GetTickCount())
    Package serial Number: INT32      (ascends from 0)
    Collection time (seconds): INT32  (Seconds since epoch)
    Audio format: INT8                (=0 adpcm)
    Data length: INT32                (=160)
    Data content: BINARY_STREAM[n]
    '''
    d = [None]*12
    d[0] = 'MO_V'
    d[1] = 3
    d[2] = 0
    d[3] = ''
    d[4] = 177
    d[5] = 177
    d[6] = (serial*40)
    d[7] = serial
    d[8] = int(time.mktime(datetime.datetime.now().timetuple()))
    d[9] = 0
    d[10] = 160
    d[11] = adpcm_data
    data = pack('<4s h b 8s I I I I I b I 160s', *d)
    return data
    
  def _unpack_data(self, format, binary_string):
    self.logger.debug('hex data: %s', binascii.hexlify(binary_string))
    data = unpack(format, binary_string)
    return data


  def create_data_connection(self, dataConnectionId):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((self._ip, self._port))

    s.send(self._login_request_data(dataConnectionId))
    return s
  
  def  send_wav(self, dataConnectionId, fileName):
    import threading
    thread = threading.Thread(target=self._send_wav, args=(dataConnectionId, fileName))
    thread.start()
    return thread
    
  def _send_wav(self, dataConnectionId, fileName):
    s = self.create_data_connection(dataConnectionId)
    
    import wave
    import audioop
    #ffmpeg -i Turret_turret_active_8.wav -ar 8k Turret_turret_active_8_8000.wav
    waveFile = wave.open(fileName, 'rb')

    length = waveFile.getnframes()
    state = None
    state_ratecv=None
    serial = 0
    bytes = []
    for i in range(0,length):
        waveData = waveFile.readframes(2) 
        adpcmfrag, state = audioop.lin2adpcm(waveData, 2, state)
        bytes.append(adpcmfrag)
        if len(bytes) == 160:
          #print bytes
          time.sleep(0.02)  # 20ms per fragment?

          data = camera.create_talk_data(serial, ''.join(bytes))
          serial += 1
          bytes = []

          s.send(data)

    if len(bytes):
      data = camera.create_talk_data(serial, ''.join(bytes))
      s.send(data)
      
    waveFile.close()
    time.sleep(1) #Wait for the audio to finish

if __name__ == '__main__':
  if len(argv) == 4:
    camera = Camera(argv[1], int(argv[2]))
    success, dataConnectionId = camera.login('admin\x00\x00\x00i\x00m\x00a', '\x00\x00g\x00e\x00s\x00/\x00x\x004')
    print 'success, dataConnectionId', success, dataConnectionId
  
    handle = camera.send_wav(dataConnectionId, argv[3])
    handle.join()

  else:
    print 'usage: python %s IP PORT wav_file.wav' % argv[0]
