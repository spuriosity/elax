require('dotenv').config()

const express = require('express');
const app = express();
const http = require('http');
const { SocketAddress } = require('net');
const server = http.createServer(app);
const { Server } = require("socket.io");
const io = new Server(server);

app.use(express.static(__dirname + '/audios'))
app.use(express.static(__dirname + '/images'))
app.use(express.static(__dirname + '/css'))


// var roomConfigs = {
//   timerSeconds: 0,
// }

actions_list = {
  'clap': 0,
  'laugh': 0,
  'boo': 0,
}


app.get('/', (req, res) => {
  res.sendFile(__dirname + '/index.html');
});

app.get('/control', (req, res) => {
  res.sendFile(__dirname + '/cp.html');
});

app.get('/shake', (req, res) => {
  res.sendFile(__dirname + '/shake.html');
});

app.get('/listen', (req, res) => {
  res.sendFile(__dirname + '/listen-timer.html');
});

app.get('/.well-known/acme-challenge/:token', function(req, res) {
  var token = req.params.token;
  res.send(token);
});

a=1;
setInterval(function(){
  io.to('listeners').emit('actions_count', actions_list)
}, 1000);


io.on('connection', (socket) => {
  console.log('a user connected');
  // socket.emit('serverConfig', serverConfig);

  socket.on('join', function (type) {    
    socket.join(type);
  });

  socket.on('disconnect', () => {
    console.log('user disconnected');
  });

  socket.on('action', (action, user_id) => {
    console.log(actions_list)
    actions_list[action]++;
    setTimeout(() => {
      actions_list[action]--
    }, 1000)
    console.log('new action triggered: ' + action + ' by user: ' + user_id);
    // io.to('listeners').emit('actions_count', actions_list)
  });

  socket.on('addTime', () => {
    io.to('listeners').emit('addTime');
  });

  socket.on('timerReset', seconds => {
    console.log('timer Reset received: ', seconds)
    io.to('listeners').emit('timerReset', seconds);
  });

  socket.on('setReactionStatus', status => {
    io.to('listeners').emit('setReactionStatus', status);
  });
});

server.listen(process.env.APP_PORT, () => {
  console.log(`listening on localhost:${process.env.APP_PORT}`);
});
