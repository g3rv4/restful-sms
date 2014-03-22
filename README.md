restful-sms
===========

This is a RESTful gateway written in python 3.3 to send and receive text messages using AT Commands. I'm using it with a Portech MV-372. This is just a bridge, so you should set up your logic somewhere else.

It also has a method to retrieve the credit remaining by using the SIM Application Toolkit, but it is hardcoded for my carrier (antel).

I'm running it with supervisor and it has been working just fine for a month now.

There're lots of things to do (this is just a first functional version) but the most important remaining things are:

* Adding tests (this is my first python program, I'm just getting started and I'm not even sure how to write tests here)
* Separating the logic of the AT Commands into devices (to be able to support multiple devices)
* Separating the carrier logic to get credits (to be able to support multiple carriers)