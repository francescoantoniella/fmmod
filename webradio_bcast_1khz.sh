#sox -n -r 48000 -c 2 -b 16 -t raw - synth sine 1000 | ./build/modulatore --stdin 
#sox -n -r 48000 -c 2 -b 16 -t raw - synth sine 1000 | ./build/modulatore --no-pluto --stdin --fm-iq |python flowgraphs/iq.py
sox -n -r 48000 -c 2 -b 16 -t raw - synth sine 1000 | ./build/modulatore --no-pluto --stdin  --fm-iq |python flowgraphs/rds_rx.py
#ffmpeg -re -i "http://nr9.newradio.it:9371/stream" -f s16le -ac 2 -ar 48000 - | ./build/modulatore  --stdin 
