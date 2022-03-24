# daily
Helps you keeping track of all the things worth to mention during your daily standup

# Demo
![demo](demo.gif)

# Usage

```
usage: daily [-h] [-d DATE] {add,get,edit,nuke,remove} ...

positional arguments:
  {add,get,edit,nuke,remove}
    add                 add one or more entries for a given day
    get                 read entries for a given day
    edit                edit entries for a given day
    nuke                delete entries for a given day
    remove              delete entries for a given day

optional arguments:
  -h, --help            show this help message and exit
  -d DATE, --date DATE  specify a date the command applies to
```

# Installation
````shell
make install
````