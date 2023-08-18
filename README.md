# hub-action

## 使用教程

1、生成SSH公钥

执行命令：`ssh-keygen -t rsa -C "youremail@example.com"`，连续三次回车，id_rsa 为`私钥`，id_rsa.pub为`公钥`
不使用默认SSH参考：[生成/添加SSH公钥](https://help.gitee.com/enterprise/code-manage/%E6%9D%83%E9%99%90%E4%B8%8E%E8%AE%BE%E7%BD%AE/%E9%83%A8%E7%BD%B2%E5%85%AC%E9%92%A5%E7%AE%A1%E7%90%86/%E7%94%9F%E6%88%90%E6%88%96%E6%B7%BB%E5%8A%A0SSH%E5%85%AC%E9%92%A5)

2、GitHub项目配置SSH密钥

在Github项目
`Settings`->`Secrets`->`Actions`，名称为：`GITEE_RSA_PRIVATE_KEY`，值为：上面生成SSH的`私钥`

![1.png](assets/1.png)
![2.png](assets/2.png)

3、GitHub配置SSH公钥

![3.png](assets/3.png)

在Github
`Settings`->`SSH and GPG keys`->`New SSH key`，名称为：`GITEE_RSA_PUBLIC_KEY`，值为：上面生成SSH的`公钥`

4、Gitee配置SSH公钥

在Gitee
`设置`->`安全设置`->`SSH公钥`，标题为：`GITEE_RSA_PUBLIC_KEY`，值为：上面生成SSH的`公钥`

![4.png](assets/4.png)

5、GitHub创建Github workflow

在Github项目
`Actions`创建一个新的workflow

![5.png](assets/5.png)
![6.png](assets/6.png)
![7.png](assets/7.png)

如果在github Action出现`remote: error: GE007: Your push would publish a private email address.`这个报错，则在gitee `设置`->`邮箱管理` , √去掉

![8.png](assets/8.png)